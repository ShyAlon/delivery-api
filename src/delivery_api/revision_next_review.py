"""Private exact continuation-result review. No HTTP caller or dispatch.

Approval binds the new retained result checkpoint, never the immutable installed
source. A fresh approved checkpoint is prepared separately; no model is invoked.
"""
import hashlib,json

STAGES={'architecture':'architecture','specs':'items','tests':'tests','validation':'validation'}
NEXT={'architecture':('specs','items'),'specs':('tests','tests'),'tests':('validation','validation')}

def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def read_private_next_review(store,task_id,*,installation_id,artifacts_base,result_reference=None,expected_job_id=None,allow_approved=False):
    from .pg_revision_next_review import read
    return read(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,result_reference=result_reference,expected_job_id=expected_job_id,allow_approved=allow_approved)


def approve_private_next_review(store,task_id,*,installation_id,artifacts_base,review_sha256,extra_cost_acknowledged,selected_option,validate_submission,result_reference=None):
    if not callable(validate_submission) or not isinstance(selected_option,str) or not selected_option.strip():raise ValueError('trusted exact submission verifier and reviewed selected option required')
    if extra_cost_acknowledged is not True:raise ValueError('explicit next continuation cost acknowledgement required')
    with store._task_operation_guard('task.gate.decide'):
        current=read_private_next_review(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,result_reference=result_reference)
        if current['review_sha256']!=review_sha256:raise ValueError('next phase review changed')
        from .tenancy import current_scope
        scope=current_scope()
        if not scope.session_id or not store.auth_session_active():raise ValueError('active authenticated owner required')
        evidence=validate_submission(current['review'],review_sha256,selected_option,extra_cost_acknowledged)
        if not isinstance(evidence,str) or not evidence:raise ValueError('authenticated submission reference required')
        receipt={'submission_reference':evidence,'selected_option':selected_option,'review_sha256':review_sha256,'auth_session_id':scope.session_id,'owner_id':scope.owner_id,'extra_cost_acknowledged':True,
            'phase':current['review']['phase'],'decision':'approve','dispatch_started':False}
        store.retain_decision_review(task_id,'revision-next-approval:'+installation_id,review_sha256,receipt)
        # Exact artifact/checkpoint basis and approval receipt commit with gate decision.
        store.decide_gate(task_id,'gate-'+receipt['phase'],'approve',selected_option=selected_option,expected_revision=current['review']['gate_revision'])
        return receipt


def successor_basis(store,task_id,*,installation_id,artifacts_base,review_sha256,expected_job_id=None):
    """Current owner, original acceptance and exact retained successor approval."""
    with store._task_operation_guard('task.rerun'):
        original=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
        reviewed=store.decision_review(task_id,'revision-next-review:'+installation_id,review_sha256)
        approval=store.decision_review(task_id,'revision-next-approval:'+installation_id,review_sha256)
        if not reviewed or not approval or not approval.get('submission_reference') or approval.get('extra_cost_acknowledged') is not True:
            raise ValueError('exact authenticated retained successor approval required')
        if approval['owner_id']!=original['capability'].scope.owner_id:raise ValueError('successor owner differs')
        # A session revoked since submission invalidates continuation authority.
        from .tenancy import current_scope
        if approval['auth_session_id']!=current_scope().session_id or not store.auth_session_active():
            raise ValueError('successor submission session revoked')
        current=read_private_next_review(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,
            result_reference=reviewed['result_reference'],expected_job_id=expected_job_id,allow_approved=True)
        for key in ('checkpoint_sha256','checkpoint','artifact_sha256','job_id','generation','phase','source_directory','source_review_sha256'):
            if current['review'][key]!=reviewed[key]:raise ValueError('retained successor review changed')
        task=original['capability'].validate(store)
        gate=next(g for g in task['gates'] if g['after_phase']==reviewed['phase'])
        phase=next(p for p in task['phases'] if p['phase_key']==reviewed['phase'])
        if (gate['state']!='decided' or phase['state']!='approved' or gate['is_open']
            or gate['revision']!=reviewed['gate_revision']+1 or gate['selected_option']!=approval['selected_option']):raise ValueError('successor phase not approved')
        if reviewed['phase'] not in NEXT:raise ValueError('terminal review has no private downstream dispatch')
        return {'capability':original['capability'],'phase':reviewed['phase'],'review_sha256':review_sha256,
            'artifact_sha256':reviewed['artifact_sha256'],'checkpoint':reviewed['checkpoint'],
            'checkpoint_sha256':reviewed['checkpoint_sha256'],'source_directory':reviewed['source_directory'],
            'approval_reference':approval['submission_reference'],'installation_id':installation_id}|{'snapshot':reviewed['snapshot']}


def prepare_private_successor(store,task_id,*,installation_id,artifacts_base,review_sha256):
    """Separate exact successor seed under installed writer fence. No invocation."""
    from .pg_revision_next_review import prepare_successor
    return prepare_successor(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=review_sha256)


def queue_private_successor(store,task_id,*,installation_id,artifacts_base,review_sha256):
    from .revision_continuation_worker import KIND
    with store._task_operation_guard('task.rerun'):
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
        marker='revision-chain-job:'+installation_id+':'+review_sha256
        prior=store.decision_review(task_id,marker,'queue')
        if prior:
            from .worker_capability import WorkerCapability
            WorkerCapability.capture(store,task_id).validate(store)
            job=store.worker_job(prior['job_id'])
            if not job or job['kind']!=KIND or job['execution_options']!={}:raise ValueError('successor queue evidence differs')
            return prior['job_id']
        basis=successor_basis(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=review_sha256)
        prepared=store.decision_review(task_id,'revision-chain-prepared:'+installation_id,review_sha256)
        if not prepared or prepared['source_checkpoint_sha256']!=basis['checkpoint_sha256']:raise ValueError('exact prepared successor required')
        job_id=store.enqueue_worker(task_id,KIND)
        store.retain_decision_review(task_id,marker,'queue',{'job_id':job_id,'review_sha256':review_sha256,'installation_id':installation_id})
        return job_id


async def execute_private_successor(store,worker,job_id,*,installation_id,artifacts_base,review_sha256,graph_factory=None):
    from .revision_continuation_worker import _execute_continuation
    return await _execute_continuation(store,worker,job_id,installation_id=installation_id,artifacts_base=artifacts_base,successor=review_sha256,graph_factory=graph_factory)
