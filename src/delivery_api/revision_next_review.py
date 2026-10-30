"""Private exact continuation-result review. No HTTP caller or dispatch.

Approval binds the new retained result checkpoint, never the immutable installed
source. A fresh approved checkpoint is prepared separately; no model is invoked.
"""
import hashlib,json,os,sqlite3,stat,re
from types import SimpleNamespace
from .tenancy import session_root
from .producer_inputs import _open_directory,_identity

STAGES={'architecture':'architecture','specs':'items','tests':'tests','validation':'validation'}
NEXT={'architecture':('specs','items'),'specs':('tests','tests'),'tests':('validation','validation')}

def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def read_private_next_review(store,task_id,*,installation_id,artifacts_base,result_reference=None,expected_job_id=None,allow_approved=False):
    if hasattr(store,'_postgres'):
        from .pg_revision_next_review import read
        return read(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,result_reference=result_reference,expected_job_id=expected_job_id,allow_approved=allow_approved)
    from .worker_capability import WorkerCapability
    from spec_generator.graph import build_graph,checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver
    with store._task_operation_guard('task.gate.decide'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
        cap=basis['capability'];root=session_root(artifacts_base,cap.scope,cap.session_id)
        reference=result_reference or basis['review_sha256']
        if not isinstance(reference,str) or not re.fullmatch('[0-9a-f]{64}',reference):raise ValueError('canonical retained result reference required')
        receipt=store.decision_review(task_id,'revision-continuation-result:'+installation_id,reference)
        if not receipt or receipt['phase'] not in STAGES:raise ValueError('retained generated next-phase result required')
        job=store.worker_job(receipt['job_id'])
        if (not job or job['state']!='done' or job['task_id']!=task_id or job['session_id']!=cap.session_id
            or job['generation']!=cap.generation or job['kind']!='continuation:approved-revision' or job['execution_options']!={}):raise ValueError('completed result worker required')
        directory=root/('spec/continuation-execution' if result_reference is None else 'spec/continuation-chain/'+reference+'/execution');parent=_open_directory(directory)
        try:
            def read(name,limit):
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('bounded review file required')
                    with os.fdopen(fd,'rb',closefd=False) as f:return f.read(limit+1)
                finally:os.close(fd)
            if json.loads(read('receipt.json',2*1024*1024))!=receipt:raise ValueError('result receipt differs')
            stage=STAGES[receipt['phase']];artifact=json.loads(read(stage+'.json',2*1024*1024))
            if _digest(artifact)!=receipt['artifact_sha256']:raise ValueError('result artifact differs')
            checkpoint=read('checkpoints.sqlite',64*1024*1024)
            for sidecar in ('checkpoints.sqlite-wal','checkpoints.sqlite-shm','checkpoints.sqlite-journal'):
                try:os.stat(sidecar,dir_fd=parent,follow_symlinks=False)
                except FileNotFoundError:continue
                raise ValueError('result checkpoint sidecar unresolved')
            fd=os.open('checkpoints.sqlite',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            try:
                conn=sqlite3.connect(f'file:/proc/self/fd/{fd}?mode=ro&immutable=1',uri=True,check_same_thread=False)
                try:
                    graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,stop_after=stage,checkpointer=SqliteSaver(conn,serde=checkpoint_serde()))
                    state=graph.get_state({'configurable':{'thread_id':cap.session_id}})
                    if state.next:raise ValueError('generated phase did not stop at review')
                    body=state.values.get(stage)
                    if stage=='architecture':
                        from spec_generator.graph import validated_architecture
                        body=validated_architecture(body).model_dump(mode='json')
                    if stage=='validation':
                        from vmodel_contracts import ValidationPlan
                        body=ValidationPlan.model_validate({'session_id':cap.execution_anchor.semantic_session_id,**body}).model_dump(mode='json')
                    if isinstance(artifact,list):
                        from vmodel_contracts import ItemSpec,AcceptanceTest
                        model=AcceptanceTest if stage=='tests' else ItemSpec
                        body=[model.model_validate(x).model_dump(mode='json') for x in body]
                    if body!=artifact:raise ValueError('result checkpoint differs from reviewed artifact')
                finally:conn.close()
            finally:os.close(fd)
            if (read('checkpoints.sqlite',64*1024*1024)!=checkpoint or json.loads(read('receipt.json',2*1024*1024))!=receipt
                or json.loads(read(stage+'.json',2*1024*1024))!=artifact):raise ValueError('review checkpoint changed')
            current=_open_directory(directory)
            try:
                if _identity(current)!=_identity(parent):raise ValueError('review namespace changed')
            finally:os.close(current)
        finally:os.close(parent)
        gate=next(g for g in cap.validate(store)['gates'] if g['after_phase']==receipt['phase'])
        if (gate['state'] not in (('waiting','decided') if allow_approved else ('waiting',)) or gate['is_open']):raise ValueError('exact next phase waiting gate required')
        review={'task_id':task_id,'installation_id':installation_id,'execution_session_id':cap.session_id,'generation':cap.generation,
            'phase':receipt['phase'],'gate_revision':gate['revision'],'source_review_sha256':reference,'result_reference':result_reference,
            'artifact_sha256':receipt['artifact_sha256'],'checkpoint_sha256':hashlib.sha256(checkpoint).hexdigest(),
            'checkpoint':state.config,'source_directory':str(directory),'job_id':receipt['job_id'],'accepted':False,'dispatch_started':False}
        digest=_digest(review)
        if not allow_approved:store.retain_decision_review(task_id,'revision-next-review:'+installation_id,digest,review)
        return {'review':review,'review_sha256':digest,'artifact':artifact}


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
            'approval_reference':approval['submission_reference'],'installation_id':installation_id}|({'snapshot':reviewed['snapshot']} if hasattr(store,'_postgres') else {})


def prepare_private_successor(store,task_id,*,installation_id,artifacts_base,review_sha256):
    """Separate exact successor seed under installed writer fence. No invocation."""
    if hasattr(store,'_postgres'):
        from .pg_revision_next_review import prepare_successor
        return prepare_successor(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=review_sha256)
    from pathlib import Path
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import check_execution,guarded_publish
    from spec_generator.stop_publication import FencedCheckpointer,publication_guard
    from spec_generator.revision_source import read_closed_checkpoint
    from spec_generator.graph import build_graph,checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver
    basis=successor_basis(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=review_sha256)
    cap=basis['capability'];root=session_root(artifacts_base,cap.scope,cap.session_id);binding=cli_stop_binding()
    if (binding is None or binding[1]!=cap.generation or getattr(binding[0],'_execution_anchor',None)!=cap.execution_anchor
        or Path(binding[0].path)!=root/'interview/stop-request.json'):raise ValueError('exact successor writer fence required')
    def authority():
        check_execution();cap.validate(store)
        if successor_basis(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=review_sha256)!=basis:
            raise ValueError('successor authority changed')
    marker='revision-chain-prepared:'+installation_id
    with store._task_operation_guard('task.rerun'):
        authority()
        if store.decision_review(task_id,marker+':intent',review_sha256):raise ValueError('successor preparation already attempted; explicit reconciliation required')
        store.retain_decision_review(task_id,marker+':intent',review_sha256,{'state':'pending','dispatch_started':False})
    source=Path(basis['source_directory'])/'checkpoints.sqlite'
    def pinned_bytes(path):
        directory=_open_directory(path.parent)
        try:
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size>64*1024*1024:raise ValueError('bounded regular successor checkpoint required')
                with os.fdopen(fd,'rb',closefd=False) as stream:return stream.read(64*1024*1024+1)
            finally:os.close(fd)
        finally:os.close(directory)
    source_raw=pinned_bytes(source)
    if hashlib.sha256(source_raw).hexdigest()!=basis['checkpoint_sha256']:raise ValueError('successor source checkpoint differs')
    values=read_closed_checkpoint(source,session_id=cap.session_id,expected_checkpoint_id=basis['checkpoint']['configurable']['checkpoint_id'])
    # Only public graph channels are seeded, never checkpoint scheduling internals.
    from spec_generator.graph import GenState
    values={k:v for k,v in values.items() if k in GenState.__annotations__}
    destination=root/'spec/continuation-chain'/review_sha256/'prepared'
    parent=_open_directory(root/'spec')
    try:
        try:guarded_publish(lambda:os.mkdir('continuation-chain',mode=0o700,dir_fd=parent))
        except FileExistsError:pass
    finally:os.close(parent)
    chain=_open_directory(root/'spec/continuation-chain')
    try:authority();guarded_publish(lambda:os.mkdir(review_sha256,mode=0o700,dir_fd=chain));os.fsync(chain)
    finally:os.close(chain)
    enclosing=_open_directory(destination.parent)
    try:guarded_publish(lambda:os.mkdir('prepared',mode=0o700,dir_fd=enclosing));os.fsync(enclosing)
    finally:os.close(enclosing)
    directory=_open_directory(destination);identity=_identity(directory)
    conn=None
    try:
        fd=guarded_publish(lambda:os.open('checkpoints.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory));os.close(fd)
        conn=sqlite3.connect(f'/proc/self/fd/{directory}/checkpoints.sqlite',check_same_thread=False)
        graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(binding[0],binding[1])))
        config={'configurable':{'thread_id':cap.session_id}}
        node={'architecture':'gate_architecture','specs':'items','tests':'tests'}[basis['phase']]
        authority();graph.update_state(config,values,as_node=node);authority();state=graph.get_state(config)
        if state.next!=(NEXT[basis['phase']][1],):raise ValueError('successor checkpoint schedule differs')
        conn.close();conn=None
        checkpoint=destination/'checkpoints.sqlite'
        fd=os.open('checkpoints.sqlite',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        try:os.fsync(fd)
        finally:os.close(fd)
        os.fsync(directory)
        raw=pinned_bytes(checkpoint)
        if pinned_bytes(source)!=source_raw:raise ValueError('successor source changed')
        receipt={'checkpoint':state.config,'checkpoint_file_sha256':hashlib.sha256(raw).hexdigest(),
            'review_sha256':review_sha256,'source_checkpoint_sha256':basis['checkpoint_sha256'],
            'approval_reference':basis['approval_reference'],'dispatch_started':False,'provider_invoked':False}
        current=_open_directory(destination)
        try:
            if _identity(current)!=identity:raise ValueError('successor namespace changed')
        finally:os.close(current)
        with store._task_operation_guard('task.rerun'):
            authority();store.retain_decision_review(task_id,marker,review_sha256,receipt)
        return receipt
    finally:
        if conn is not None:conn.close()
        os.close(directory)


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
