"""Immutable private PG staging, no installation/session claim or model calls."""
import hashlib,json
from types import SimpleNamespace
from .generator_snapshot import GeneratorSnapshots

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate_lineage(capability,lineage,lineage_sha256):
    if digest(lineage)!=lineage_sha256:raise ValueError('exact stage lineage digest required')
    if (lineage.get('kind')!='closed_revision_execution_lineage' or type(lineage.get('generation')) is not int
        or type(lineage.get('parent_generation')) is not int):raise ValueError('exact typed stage lineage required')
    if any(lineage.get(k)!=v for k,v in (('owner_id',capability.scope.owner_id),('task_id',capability.task_id),
        ('parent_execution_session_id',capability.session_id),('parent_generation',capability.generation),
        ('generation',capability.generation+1),('state','planned'),('installed',False),('dispatch_started',False))):
        raise ValueError('stage lineage differs from captured owner')

def retain_stage(store,capability,*,source,result,lineage,lineage_sha256,change_request,validate_authority,upstream=None):
    from langgraph.checkpoint.memory import MemorySaver
    from spec_generator.graph import build_graph,checkpoint_serde
    from spec_generator.revision_checkpoint import stage_private_lineage_checkpoint
    from .revision_checkpoint_reader import read_source_checkpoint
    if not callable(validate_authority):raise ValueError('current stage authority required')
    validate_lineage(capability,lineage,lineage_sha256)
    snapshots=GeneratorSnapshots(store,capability)
    with snapshots._transaction():
        validate_authority()
        current=read_source_checkpoint(store,capability,None,session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
        if current!=source:raise ValueError('stage source differs from owned checkpoint')
        marker='pg-revision-stage:'+lineage_sha256
        if store.decision_review(capability.task_id,marker,'prepared'):raise ValueError('stage already exists; explicit reconciliation required')
        saver=MemorySaver(serde=checkpoint_serde())
        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=saver)
        cfg={'configurable':{'thread_id':lineage['execution_session_id']}}
        prepared=stage_private_lineage_checkpoint(source=source,proposal_result=result,lineage=lineage,change_request=change_request,
            graph=graph,config=cfg,validate_authority=validate_authority)
        snapshot=snapshots._retain_prepared(saver,cfg,purpose='revision-stage')
        import base64
        documents={name:base64.b64encode(raw).decode() for name,raw in (upstream or {}).items()}
        body={'kind':'pg_closed_revision_stage','lineage_sha256':lineage_sha256,'snapshot':snapshot,
            'checkpoint_id':prepared['destination_checkpoint']['configurable']['checkpoint_id'],'change_request':change_request,
            'upstream':documents}
        sha=digest(body)
        validate_authority()
        store.retain_decision_review(capability.task_id,marker,'prepared',body)
        return {'manifest_sha256':sha,'checkpoint_id':body['checkpoint_id']}

def read_stage(store,capability,receipt,*,lineage,lineage_sha256):
    from spec_generator.graph import build_graph
    from spec_generator.revision_checkpoint import validate_staged_lineage_state
    from .revision_checkpoint_reader import read_source_checkpoint
    validate_lineage(capability,lineage,lineage_sha256)
    if not isinstance(receipt,dict) or set(receipt)!={'manifest_sha256','checkpoint_id'}:raise ValueError('exact stage receipt required')
    snapshots=GeneratorSnapshots(store,capability)
    with snapshots._transaction(read_only=True):
        body=store.decision_review(capability.task_id,'pg-revision-stage:'+lineage_sha256,'prepared')
        if (not body or body.get('kind')!='pg_closed_revision_stage' or body.get('lineage_sha256')!=lineage_sha256
            or digest(body)!=receipt['manifest_sha256'] or body['checkpoint_id']!=receipt['checkpoint_id']):raise ValueError('stage receipt digest differs')
        reader=snapshots.reader(body['snapshot'])
        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=reader)
        state=graph.get_state({'configurable':{'thread_id':lineage['execution_session_id']}})
        if state.next!=('gate_'+lineage['phase'],):raise ValueError('stage must await fresh edited gate')
        source=read_source_checkpoint(store,capability,None,session_id=capability.session_id,expected_checkpoint_id=lineage['source_checkpoint_id'])
        validate_staged_lineage_state(source=source,staged_state=state.values,lineage=lineage,change_request=body['change_request'])
        import base64
        upstream={name:base64.b64decode(raw,validate=True) for name,raw in body['upstream'].items()}
        return {'state':state,'snapshot':body['snapshot'],'receipt':receipt,'upstream':upstream}
