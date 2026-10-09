"""Private one-job approved phase continuation consumer. No routes or queue scans.

The prepared checkpoint stays immutable. Runtime output goes to a fresh exclusive
namespace. Ambiguous/failed jobs and partial output never auto-resume. The next
architecture result waits at its own closed gate; no downstream auto-approval.
"""
import hashlib,json,os,sqlite3,stat
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from .revision_continuation import reconcile_private_approved_continuation
from .producer_inputs import _open_directory,_identity
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
        next_phase='architecture' if basis['phase']=='srs' else 'specs'
        gate=next(g for g in store.task(task_id)['gates'] if g['after_phase']==next_phase)
        if gate['is_open']:raise ValueError('next phase explicit closed review required')
        observed=reconcile_private_approved_continuation(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base)
        if observed['state']!='continuation_prepared_verified_no_dispatch':raise ValueError('verified prepared continuation required')
        job_id=store.enqueue_worker(task_id,KIND)
        store.retain_decision_review(task_id,marker,'queue',{'job_id':job_id,'review_sha256':basis['review_sha256'],'installation_id':installation_id})
        return job_id

async def execute_private_approved_continuation(store,worker,job_id,*,installation_id,artifacts_base,graph_factory=None):
    return await _execute_continuation(store,worker,job_id,installation_id=installation_id,artifacts_base=artifacts_base,graph_factory=graph_factory)

async def _execute_continuation(store,worker,job_id,*,installation_id,artifacts_base,graph_factory=None,successor=None):
    job=store.worker_job(job_id)
    if not job or job['kind']!=KIND or job['execution_options']!={}:raise ValueError('exact continuation job required')
    marker=store.decision_review(job['task_id'],('revision-continuation-job:'+installation_id if successor is None else 'revision-chain-job:'+installation_id+':'+successor),'queue')
    if not marker or marker['job_id']!=job_id:raise ValueError('retained continuation job binding required')
    outputs=[]
    def producer(cap,root):
        with ExitStack() as resources:
            from spec_generator.cooperative_run import cli_stop_binding
            from spec_generator.stop_fence import check_execution,guarded_publish
            from spec_generator.stop_publication import FencedCheckpointer,publication_guard
            from spec_generator.worker_runtime import current_runtime
            from spec_generator.graph import build_graph,checkpoint_serde,validated_architecture,_check_baseline_reconciliation,_check_decision_drivers
            from vmodel_contracts import SystemBaseline
            from langgraph.checkpoint.sqlite import SqliteSaver
            def get_basis():
                if successor is None:return store.private_approved_continuation_basis(cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=job_id)
                from .revision_next_review import successor_basis
                return successor_basis(store,cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,review_sha256=successor,expected_job_id=job_id)
            basis=get_basis()
            if basis['phase'] not in ('srs','architecture','specs','tests') or marker['review_sha256']!=basis['review_sha256']:raise ValueError('exact phase continuation approval required')
            stage={'srs':'architecture','architecture':'items','specs':'tests','tests':'validation'}[basis['phase']]
            next_phase={'architecture':'architecture','items':'specs','tests':'tests','validation':'validation'}[stage]
            runtime=current_runtime();binding=cli_stop_binding()
            expected=(cap.scope.owner_id,cap.task_id,job_id,cap.generation,str(root))
            if (runtime is None or runtime.worker_binding!=expected or binding is None
                or binding[1]!=cap.generation or getattr(binding[0],'_execution_anchor',None)!=cap.execution_anchor
                or Path(binding[0].path)!=root/'interview/stop-request.json' or stage not in runtime.models):
                raise ValueError('bound continuation runtime required')
            def authority():
                check_execution();cap.validate(store)
                if get_basis()!=basis:
                    raise ValueError('continuation approval basis changed')
            authority()
            observed=(reconcile_private_approved_continuation(store,cap.task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=job_id) if successor is None else {'state':'continuation_prepared_verified_no_dispatch'})
            if observed['state']!='continuation_prepared_verified_no_dispatch':raise ValueError('continuation preparation differs')
            prepared=(root/'spec/approved-continuation/checkpoints.sqlite' if successor is None else root/'spec/continuation-chain'/successor/'prepared/checkpoints.sqlite')
            if prepared.resolve()!=prepared:raise ValueError('unsafe prepared checkpoint')
            for suffix in ('-wal','-shm','-journal'):
                if os.path.lexists(str(prepared)+suffix):raise ValueError('prepared checkpoint sidecar unresolved')
            prepared_dir=_open_directory(prepared.parent);resources.callback(os.close,prepared_dir)
            prepared_identity=_identity(prepared_dir)
            fd=os.open(prepared.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=prepared_dir)
            prepared_stamp=os.fstat(fd)
            if not stat.S_ISREG(prepared_stamp.st_mode) or prepared_stamp.st_size>64*1024*1024:
                os.close(fd);raise ValueError('bounded regular prepared checkpoint required')
            try:
                with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(64*1024*1024+1)
            finally:os.close(fd)
            if len(raw)>64*1024*1024:raise ValueError('bounded checkpoint required')
            destination=(root/'spec/continuation-execution' if successor is None else root/'spec/continuation-chain'/successor/'execution')
            if destination.resolve()!=destination:raise ValueError('unsafe continuation destination')
            enclosing=_open_directory(destination.parent);resources.callback(os.close,enclosing)
            guarded_publish(lambda:os.mkdir(destination.name,mode=0o700,dir_fd=enclosing));os.fsync(enclosing)
            destination_fd=_open_directory(destination)
            destination_identity=_identity(destination_fd)
            resources.callback(os.close,destination_fd)
            def namespace():
                fd=_open_directory(destination)
                try:
                    if _identity(fd)!=destination_identity:raise ValueError('continuation destination changed')
                finally:os.close(fd)
            checkpoint=destination/'checkpoints.sqlite'
            def write():
                namespace()
                fd=os.open('checkpoints.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=destination_fd)
                with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            guarded_publish(write)
            os.fsync(destination_fd)
            authority()
            prepared_receipt=store.decision_review(cap.task_id,('revision-continuation:'+installation_id+':prepared' if successor is None else 'revision-chain-prepared:'+installation_id),basis['review_sha256'])
            if (not prepared_receipt or prepared_receipt['review_sha256']!=basis['review_sha256']
                or (successor is not None and (prepared_receipt['source_checkpoint_sha256']!=basis['checkpoint_sha256']
                    or prepared_receipt['approval_reference']!=basis['approval_reference']))
                or hashlib.sha256(raw).hexdigest()!=prepared_receipt['checkpoint_file_sha256']):
                raise ValueError('prepared checkpoint changed before copy')
            namespace()
            conn=sqlite3.connect(f'/proc/self/fd/{destination_fd}/checkpoints.sqlite',check_same_thread=False)
            try:
                graph=(graph_factory or build_graph)(SimpleNamespace(record=lambda *a:None),review=True,stop_after=stage,require_decisions=True,
                    checkpointer=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(binding[0],binding[1])))
                config={'configurable':{'thread_id':cap.session_id}}
                before=graph.get_state(config)
                if before.next!=(stage,) or before.config!=prepared_receipt['checkpoint']:
                    raise ValueError('exact prepared next node required')
                result=graph.invoke(None,config);authority()
                after=graph.get_state(config)
                if after.next:raise ValueError('continuation must stop before next approval')
                upstream=('intent_model','conops','needs','system_baseline','srs')+ (() if stage=='architecture' else ('architecture',))+ (('items',) if stage in ('tests','validation') else ()) + (('tests',) if stage=='validation' else ())
                for key in upstream:
                    if result.get(key)!=before.values.get(key):raise ValueError('continuation changed approved '+key)
                known={r['requirement_id'] for section in ('functional','non_functional') for r in result['srs'][section]}
                if stage=='architecture':
                    artifact=validated_architecture(result.get('architecture'))
                    if artifact.session_id!=cap.execution_anchor.semantic_session_id:raise ValueError('continuation semantic identity differs')
                    _check_baseline_reconciliation(artifact,SystemBaseline.model_validate(result['system_baseline']))
                    _check_decision_drivers(artifact,result['intent_model'],result.get('conops'))
                    if not artifact.components or any(not set(c.requirement_ids)<=known for c in artifact.components):raise ValueError('continuation requirement trace differs')
                    if set(r for c in artifact.components for r in c.requirement_ids)!=known:raise ValueError('continuation requirement coverage incomplete')
                    body=artifact.model_dump(mode='json')
                elif stage=='items':
                    from spec_generator.architecture_decisions import require_decided
                    require_decided(result['architecture'])
                    from vmodel_contracts import ItemSpec,check_compatible
                    from spec_generator.graph import _check_item_refs
                    items=result.get('items')
                    if not isinstance(items,list) or not items:raise ValueError('typed item specifications required')
                    for item in items:
                        if 'contract_version' in item:check_compatible(item)  # Generated body gets its current stamp through the model, never restamp explicit old versions.
                    typed=[ItemSpec.model_validate(item) for item in items]
                    _check_item_refs([t.model_dump(mode='json') for t in typed],list(known),key='spec_id')
                    if any(not item.body_markdown.strip() for item in typed):raise ValueError('nonempty specification body required')
                    body=[item.model_dump(mode='json') for item in typed]
                elif stage=='tests':
                    from vmodel_contracts import AcceptanceTest,check_compatible
                    for item in result['tests']:
                        if 'contract_version' in item:check_compatible(item)
                    from spec_generator.graph import check_runner_execution,check_test_components
                    body=[AcceptanceTest.model_validate(x).model_dump(mode='json') for x in result['tests']]
                    check_runner_execution(body);check_test_components(body,result['srs'],result['items'],result['architecture'])
                    from spec_generator.skeleton import build_test_skeleton
                    expected_tests=build_test_skeleton(result['srs'],result['items'])
                    if (not body or len({x['test_id'] for x in body})!=len(body)
                        or {(x['test_id'],x['level'],x.get('criterion_id'),x.get('spec_id')) for x in body}
                            !={(x['test_id'],x['level'],x.get('criterion_id'),x.get('spec_id')) for x in expected_tests}):raise ValueError('unique complete tests required')
                else:
                    from vmodel_contracts import ValidationPlan,check_compatible
                    if 'contract_version' in result[stage]:check_compatible(result[stage])
                    body=ValidationPlan.model_validate({'session_id':cap.execution_anchor.semantic_session_id,**result[stage]}).model_dump(mode='json')
                    if body['session_id']!=cap.execution_anchor.semantic_session_id:raise ValueError('continuation semantic identity differs')
                forbidden=('items','tests','validation','bundle') if stage=='architecture' else (('tests','validation','bundle') if stage=='items' else (('validation','bundle') if stage=='tests' else ('bundle',)))
                if any(result.get(k) not in (None,[],{}) for k in forbidden):raise ValueError('continuation escaped next-phase boundary')
                receipt={'job_id':job_id,'installation_id':installation_id,'review_sha256':basis['review_sha256'],
                    'phase':next_phase,'artifact_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'requires_phase_approval':True,'downstream_dispatched':False}
                def publish():
                    for name,value in ((stage+'.json',body),('receipt.json',receipt)):
                        namespace()
                        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=destination_fd)
                        with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True,indent=2);f.flush();os.fsync(f.fileno())
                    os.fsync(destination_fd)
                # No result becomes reviewable while accepted provider work is unsettled.
                with store._task_operation_guard('task.rerun'):
                    if store._db.execute("SELECT 1 FROM provider_attempts WHERE job_id=? AND state IN ('reserved','dispatched','uncertain') LIMIT 1",(job_id,)).fetchone():
                        raise ValueError('continuation provider settlement unresolved')
                def preparation_unchanged():
                    current=_open_directory(prepared.parent)
                    try:
                        if _identity(current)!=prepared_identity:raise ValueError('prepared namespace changed')
                        now=os.stat(prepared.name,dir_fd=current,follow_symlinks=False)
                        stamp=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
                        if stamp(now)!=stamp(prepared_stamp):raise ValueError('prepared checkpoint changed')
                        for suffix in ('-wal','-shm','-journal'):
                            try:os.stat(prepared.name+suffix,dir_fd=current,follow_symlinks=False)
                            except FileNotFoundError:continue
                            raise ValueError('prepared sidecar appeared')
                    finally:os.close(current)
                authority();namespace();preparation_unchanged();guarded_publish(publish);namespace()
                with store._task_operation_guard('task.rerun'):
                    authority();namespace();preparation_unchanged()
                    gate=next(g for g in store.task(cap.task_id)['gates'] if g['after_phase']==next_phase)
                    if gate['is_open']:raise ValueError('next phase closed review changed')
                    store.retain_decision_review(cap.task_id,'revision-continuation-result:'+installation_id,basis['review_sha256'],receipt)
                    store.add_task_artifact(cap.task_id,next_phase,stage+'.json',str(destination/(stage+'.json')))
                    store.wait_at_gate(cap.task_id,next_phase)
                preparation_unchanged()
                outputs.append(receipt)
            finally:conn.close()
    started=await worker.execute(job_id,producer)
    return {'started':started,'job_id':job_id,'outputs':outputs,'downstream_dispatched':False}
