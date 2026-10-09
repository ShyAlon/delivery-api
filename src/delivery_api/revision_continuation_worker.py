"""Private one-job approved SRS continuation consumer. No routes or queue scans.

The prepared checkpoint stays immutable. Runtime output goes to a fresh exclusive
namespace. Ambiguous/failed jobs and partial output never auto-resume. The next
architecture result waits at its own closed gate; no downstream auto-approval.
"""
import hashlib,json,os,sqlite3
from pathlib import Path
from types import SimpleNamespace
from .revision_continuation import reconcile_private_approved_continuation
KIND='continuation:approved-revision'

def queue_private_approved_continuation(store,task_id,*,installation_id,artifacts_base):
    with store._task_operation_guard('task.rerun'):
        store._db.execute('SELECT task_id FROM tasks WHERE task_id=? FOR UPDATE',(task_id,)).fetchone()
        marker='revision-continuation-job:'+installation_id
        prior=store.decision_review(task_id,marker,'queue')
        if prior:
            from .worker_capability import WorkerCapability
            WorkerCapability.capture(store,task_id).validate(store)
            job=store.worker_job(prior['job_id'])
            if not job or job['kind']!=KIND or job['execution_options']!={}:raise ValueError('continuation queue evidence differs')
            return job['job_id']
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
        if basis['phase']!='srs':raise ValueError('architecture continuation consumer required for SRS only')
        gate=next(g for g in store.task(task_id)['gates'] if g['after_phase']=='architecture')
        if gate['is_open']:raise ValueError('next phase explicit closed review required')
        observed=reconcile_private_approved_continuation(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base)
        if observed['state']!='continuation_prepared_verified_no_dispatch':raise ValueError('verified prepared continuation required')
        job_id=store.enqueue_worker(task_id,KIND)
        store.retain_decision_review(task_id,marker,'queue',{'job_id':job_id,'review_sha256':basis['review_sha256'],'installation_id':installation_id})
        return job_id

async def execute_private_approved_continuation(store,worker,job_id,*,installation_id,artifacts_base,graph_factory=None):
    job=store.worker_job(job_id)
    if not job or job['kind']!=KIND or job['execution_options']!={}:raise ValueError('exact continuation job required')
    marker=store.decision_review(job['task_id'],'revision-continuation-job:'+installation_id,'queue')
    if not marker or marker['job_id']!=job_id:raise ValueError('retained continuation job binding required')
    outputs=[]
    def producer(cap,root):
        from spec_generator.cooperative_run import cli_stop_binding
        from spec_generator.stop_fence import check_execution,guarded_publish
        from spec_generator.stop_publication import FencedCheckpointer,publication_guard
        from spec_generator.worker_runtime import current_runtime
        from spec_generator.graph import build_graph,checkpoint_serde,validated_architecture,_check_baseline_reconciliation,_check_decision_drivers
        from vmodel_contracts import SystemBaseline
        from langgraph.checkpoint.sqlite import SqliteSaver
        basis=store.private_approved_continuation_basis(cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=job_id)
        if basis['phase']!='srs' or marker['review_sha256']!=basis['review_sha256']:raise ValueError('exact SRS continuation approval required')
        runtime=current_runtime();binding=cli_stop_binding()
        expected=(cap.scope.owner_id,cap.task_id,job_id,cap.generation,str(root))
        if (runtime is None or runtime.worker_binding!=expected or binding is None
            or binding[1]!=cap.generation or getattr(binding[0],'_execution_anchor',None)!=cap.execution_anchor
            or Path(binding[0].path)!=root/'interview/stop-request.json' or 'architecture' not in runtime.models):
            raise ValueError('bound continuation runtime required')
        def authority():
            check_execution();cap.validate(store)
            if store.private_approved_continuation_basis(cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=job_id)!=basis:
                raise ValueError('continuation approval basis changed')
        authority()
        observed=reconcile_private_approved_continuation(store,cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=job_id)
        if observed['state']!='continuation_prepared_verified_no_dispatch':raise ValueError('continuation preparation differs')
        prepared=root/'spec/approved-continuation/checkpoints.sqlite'
        if prepared.resolve()!=prepared:raise ValueError('unsafe prepared checkpoint')
        fd=os.open(prepared,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(64*1024*1024+1)
        finally:os.close(fd)
        if len(raw)>64*1024*1024:raise ValueError('bounded checkpoint required')
        destination=root/'spec/continuation-execution'
        guarded_publish(lambda:destination.mkdir(mode=0o700))
        checkpoint=destination/'checkpoints.sqlite'
        def write():
            fd=os.open(checkpoint,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        guarded_publish(write)
        fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        authority()
        prepared_receipt=store.decision_review(cap.task_id,'revision-continuation:'+installation_id+':prepared',basis['review_sha256'])
        if hashlib.sha256(raw).hexdigest()!=prepared_receipt['checkpoint_file_sha256']:
            raise ValueError('prepared checkpoint changed before copy')
        conn=sqlite3.connect(str(checkpoint),check_same_thread=False)
        try:
            graph=(graph_factory or build_graph)(SimpleNamespace(record=lambda *a:None),review=True,stop_after='architecture',
                checkpointer=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(binding[0],binding[1])))
            config={'configurable':{'thread_id':cap.session_id}}
            before=graph.get_state(config)
            if before.next!=('architecture',) or before.config!=prepared_receipt['checkpoint']:
                raise ValueError('exact prepared next node required')
            result=graph.invoke(None,config);authority()
            after=graph.get_state(config)
            if after.next:raise ValueError('continuation must stop before next approval')
            for key in ('intent_model','conops','needs','system_baseline','srs'):
                if result.get(key)!=before.values.get(key):raise ValueError('continuation changed approved '+key)
            artifact=validated_architecture(result.get('architecture'))
            if artifact.session_id!=cap.execution_anchor.semantic_session_id:raise ValueError('continuation semantic identity differs')
            _check_baseline_reconciliation(artifact,SystemBaseline.model_validate(result['system_baseline']))
            _check_decision_drivers(artifact,result['intent_model'],result.get('conops'))
            known={r['requirement_id'] for section in ('functional','non_functional') for r in result['srs'][section]}
            if not artifact.components or any(not set(c.requirement_ids)<=known for c in artifact.components):raise ValueError('continuation requirement trace differs')
            if set(r for c in artifact.components for r in c.requirement_ids)!=known:raise ValueError('continuation requirement coverage incomplete')
            if any(result.get(k) not in (None,[],{}) for k in ('items','tests','validation','bundle')):raise ValueError('continuation escaped next-phase boundary')
            body=artifact.model_dump(mode='json');receipt={'job_id':job_id,'installation_id':installation_id,'review_sha256':basis['review_sha256'],
                'phase':'architecture','artifact_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'requires_phase_approval':True,'downstream_dispatched':False}
            def publish():
                for name,value in (('architecture.json',body),('receipt.json',receipt)):
                    fd=os.open(destination/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True,indent=2);f.flush();os.fsync(f.fileno())
                fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            authority();guarded_publish(publish)
            with store._task_operation_guard('task.rerun'):
                authority()
                gate=next(g for g in store.task(cap.task_id)['gates'] if g['after_phase']=='architecture')
                if gate['is_open']:raise ValueError('next phase closed review changed')
                store.retain_decision_review(cap.task_id,'revision-continuation-result:'+installation_id,basis['review_sha256'],receipt)
                store.add_task_artifact(cap.task_id,'architecture','architecture.json',str(destination/'architecture.json'))
                store.wait_at_gate(cap.task_id,'architecture')
            if prepared.read_bytes()!=raw:raise ValueError('immutable preparation changed')
            outputs.append(receipt)
        finally:conn.close()
    started=await worker.execute(job_id,producer)
    return {'started':started,'job_id':job_id,'outputs':outputs,'downstream_dispatched':False}
