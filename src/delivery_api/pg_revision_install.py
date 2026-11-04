"""Accepted PG installation effects. No filesystem authority or worker grant."""
import hashlib,json
from contextlib import contextmanager
from .tenancy import current_scope
from .historical_checkpoint import HistoricalCheckpointCapability
from .pg_revision_stage import digest,read_stage
from .revision_install_plan import _validate_plan


def anchor_sha(anchor):
    return hashlib.sha256(json.dumps(anchor.document(),sort_keys=True,indent=2).encode()).hexdigest()


def _held(store,task_id,installation_id):
    intent=store._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
    if not intent:raise KeyError(installation_id)
    plan=json.loads(intent['payload']);anchor=_validate_plan(plan,intent['plan_sha256'])
    row=store._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s',(intent['lineage_id'],)).fetchone()
    if not row:raise ValueError('held installation lineage missing')
    lineage=json.loads(row['payload'])
    if digest(lineage)!=row['fingerprint'] or anchor.lineage_sha256!=row['fingerprint']:raise ValueError('held installation lineage digest differs')
    cap=HistoricalCheckpointCapability(current_scope(),task_id,lineage['parent_execution_session_id'],lineage['parent_generation'])
    cap.validate(store)
    stage=read_stage(store,cap,plan['staged_receipt'],lineage=lineage,lineage_sha256=row['fingerprint'])
    return intent,plan,anchor,stage


@contextmanager
def destination_guard(store,task_id,installation_id):
    # Session advisory lock spans the deliberately separate journal commits.
    # A second process fails before preparing any durable installation effect.
    with store._task_operation_guard('task.rerun'):
        intent,plan,anchor,stage=_held(store,task_id,installation_id)
        key='pg-revision-install:'+current_scope().owner_id+':'+anchor.execution_session_id
    with store._postgres.transaction() as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired',(key,)).fetchone()['acquired']:
            raise BlockingIOError('installation destination held')
        try:yield
        finally:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))


def prepare_anchor(store,task_id,installation_id,validate_authority):
    validate_authority()
    intent,plan,anchor,stage=_held(store,task_id,installation_id)
    if store.session_binding(anchor.execution_session_id):raise ValueError('installation destination already bound')
    marker='pg-install-anchor:'+installation_id
    if store.decision_review(task_id,marker,intent['plan_sha256']):raise ValueError('anchor already prepared; explicit reconciliation required')
    proof={'kind':'pg_revision_install_anchor','anchor':anchor.document(),'anchor_sha256':anchor_sha(anchor),
        'snapshot':stage['snapshot'],'staged_receipt':plan['staged_receipt']}
    validate_authority()
    store.retain_decision_review(task_id,marker,intent['plan_sha256'],proof)
    return {'manifest_sha256':plan['staged_receipt']['manifest_sha256'],'anchor_sha256':proof['anchor_sha256']}


def read_evidence(store,task_id,installation_id,*,promoted=True):
    intent,plan,anchor,stage=_held(store,task_id,installation_id)
    proof=store.decision_review(task_id,'pg-install-anchor:'+installation_id,intent['plan_sha256'])
    expected={'kind':'pg_revision_install_anchor','anchor':anchor.document(),'anchor_sha256':anchor_sha(anchor),
        'snapshot':stage['snapshot'],'staged_receipt':plan['staged_receipt']}
    if proof!=expected:raise ValueError('prepared PG installation anchor differs')
    if promoted:
        receipt=store.decision_review(task_id,'pg-install-promoted:'+installation_id,intent['plan_sha256'])
        if receipt!={'kind':'pg_revision_promoted','proof_sha256':digest(proof),'execution_session_id':anchor.execution_session_id}:
            raise ValueError('promoted PG installation receipt differs')
    return {'manifest_sha256':plan['staged_receipt']['manifest_sha256'],'anchor_sha256':proof['anchor_sha256']}


def promote_stage(store,task_id,installation_id,validate_authority):
    with store._task_operation_guard('task.rerun'):
        validate_authority()
        intent,plan,anchor,stage=_held(store,task_id,installation_id)
        if store.session_binding(anchor.execution_session_id):raise ValueError('installation destination already bound')
        latest=store._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
        if not latest or latest['state']!='promotion_pending':raise ValueError('exact promotion-pending journal required')
        evidence=read_evidence(store,task_id,installation_id,promoted=False)
        if json.loads(latest['payload'])['evidence']!=evidence:raise ValueError('promotion-pending evidence differs')
        marker='pg-install-promoted:'+installation_id
        if store.decision_review(task_id,marker,intent['plan_sha256']):raise ValueError('promotion exists; explicit reconciliation required')
        proof=store.decision_review(task_id,'pg-install-anchor:'+installation_id,intent['plan_sha256'])
        validate_authority()
        store.retain_decision_review(task_id,marker,intent['plan_sha256'],{'kind':'pg_revision_promoted','proof_sha256':digest(proof),'execution_session_id':anchor.execution_session_id})
        return evidence


def seed_accepted_checkpoint(store,task_id,installation_id,validate_acceptance):
    """Only inside activation's locked, accepted, installed-binding transaction.

    This narrow operation imports the reviewed in-DB snapshot into the newly
    inserted destination session. It cannot write an existing checkpoint, run a
    model, or grant a reusable worker writer. All effects roll back with CAS.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from spec_generator.graph import checkpoint_serde
    from copy import deepcopy
    intent,plan,anchor,stage=_held(store,task_id,installation_id)
    conn=store._connection.get()
    if conn is None:raise ValueError('accepted activation transaction required')
    task=store.task(task_id)
    if task['generation']!=plan['expected_parent']['generation'] or task['engine_session_id']!=plan['expected_parent']['execution_session_id']:
        raise ValueError('accepted parent CAS required')
    latest=store._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
    if not latest or latest['state']!='install_pending' or json.loads(latest['payload'])['evidence']!=read_evidence(store,task_id,installation_id):
        raise ValueError('accepted install-pending evidence required')
    binding=store._execute_native('SELECT * FROM installed_execution_bindings WHERE session_id=%s',(anchor.execution_session_id,)).fetchone()
    if (not binding or binding['installation_id']!=installation_id or binding['task_id']!=task_id
        or str(binding['owner_id'])!=current_scope().owner_id or str(binding['auth_session_id'])!=current_scope().session_id
        or binding['generation']!=anchor.generation or binding['lineage_sha256']!=anchor.lineage_sha256
        or binding['anchor_sha256']!=anchor_sha(anchor) or binding['semantic_session_id']!=anchor.semantic_session_id):
        raise ValueError('accepted pending installed binding required')
    review=store.decision_review(task_id,'revision-install:'+intent['lineage_id'],intent['review_sha256'])
    validate_acceptance(review,intent['review_sha256'],intent['acceptance_reference'])
    class AcceptedSnapshotWriter(PostgresSaver):
        @contextmanager
        def _cursor(self,*,pipeline=False):
            with conn.cursor(binary=True) as cursor:yield cursor
    saver=AcceptedSnapshotWriter(conn,serde=checkpoint_serde())
    cfg={'configurable':{'thread_id':anchor.execution_session_id,'checkpoint_ns':'generator:spec'}}
    if saver.get_tuple(cfg) is not None:raise ValueError('accepted checkpoint destination must be empty')
    from .generator_snapshot import GeneratorSnapshots
    cap=HistoricalCheckpointCapability(current_scope(),task_id,plan['expected_parent']['execution_session_id'],plan['expected_parent']['generation'])
    chain=GeneratorSnapshots(store,cap).read(stage['snapshot'],_chain=True)
    for row,paths in reversed(chain):
        checkpoint=deepcopy(row.checkpoint);parent=deepcopy(cfg)
        if row.parent_config:parent['configurable']['checkpoint_id']=row.parent_config['configurable']['checkpoint_id']
        saved=saver.put(parent,checkpoint,deepcopy(row.metadata),checkpoint['channel_versions'])
        grouped={}
        for write_task,channel,value in row.pending_writes or []:grouped.setdefault(write_task,[]).append((channel,value))
        for write_task,writes in grouped.items():saver.put_writes(saved,writes,write_task,paths[write_task])
        observed=saver.get_tuple(saved)
        if observed.checkpoint!=row.checkpoint or observed.pending_writes!=row.pending_writes:raise ValueError('accepted checkpoint readback differs')
    if saver.get_tuple(cfg).config['configurable']['checkpoint_id']!=plan['staged_receipt']['checkpoint_id']:
        raise ValueError('accepted checkpoint identity differs')
