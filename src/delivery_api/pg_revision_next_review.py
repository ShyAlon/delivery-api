"""PG next-phase review and approved successor preparation, no dispatch."""
from types import SimpleNamespace
from .pg_revision_stage import digest
from .generator_snapshot import GeneratorSnapshots


def read(store,task_id,*,installation_id,artifacts_base,result_reference=None,expected_job_id=None,allow_approved=False):
    from .revision_next_review import STAGES
    from spec_generator.graph import build_graph
    import re
    with store._task_operation_guard('task.gate.decide'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
        cap=basis['capability'];reference=result_reference or basis['review_sha256']
        if not isinstance(reference,str) or not re.fullmatch('[0-9a-f]{64}',reference):raise ValueError('canonical retained result reference required')
        receipt=store.decision_review(task_id,'revision-continuation-result:'+installation_id,reference)
        if not receipt or receipt['phase'] not in STAGES:raise ValueError('retained generated next-phase result required')
        job=store.worker_job(receipt['job_id'])
        cap.validate_job(job,state='done')
        if (job['kind']!='continuation:approved-revision' or job['execution_options']!={}):raise ValueError('completed result worker required')
        stage=STAGES[receipt['phase']];artifact=receipt['artifact']
        if digest(artifact)!=receipt['artifact_sha256']:raise ValueError('result artifact differs')
        reader=GeneratorSnapshots(store,cap).reader(receipt['snapshot'])
        state=build_graph(SimpleNamespace(record=lambda *a:None),review=True,stop_after=stage,checkpointer=reader).get_state({'configurable':{'thread_id':cap.session_id}})
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
        gate=next(g for g in cap.validate(store)['gates'] if g['after_phase']==receipt['phase'])
        if gate['state'] not in (('waiting','decided') if allow_approved else ('waiting',)) or gate['is_open']:raise ValueError('exact next phase waiting gate required')
        review={'task_id':task_id,'installation_id':installation_id,'execution_session_id':cap.session_id,'generation':cap.generation,
            'phase':receipt['phase'],'gate_revision':gate['revision'],'source_review_sha256':reference,'result_reference':result_reference,
            'artifact_sha256':receipt['artifact_sha256'],'checkpoint_sha256':receipt['snapshot']['sha256'],'checkpoint':state.config,
            'source_directory':'pg-snapshot:'+receipt['snapshot']['snapshot_id'],'snapshot':receipt['snapshot'],
            'job_id':receipt['job_id'],'accepted':False,'dispatch_started':False}
        sha=digest(review)
        if not allow_approved:store.retain_decision_review(task_id,'revision-next-review:'+installation_id,sha,review)
        return {'review':review,'review_sha256':sha,'artifact':artifact}


def prepare_successor(store,task_id,*,installation_id,artifacts_base,review_sha256):
    from pathlib import Path
    from .revision_next_review import successor_basis,NEXT
    from .tenancy import session_root
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import check_execution
    from spec_generator.graph import build_graph,checkpoint_serde,GenState
    from langgraph.checkpoint.memory import MemorySaver
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
    with store._task_operation_guard('task.rerun'):
        authority();snapshots=GeneratorSnapshots(store,cap)
        row=snapshots.read(basis['snapshot'])
        if row.config!=basis['checkpoint'] or basis['snapshot']['sha256']!=basis['checkpoint_sha256']:raise ValueError('successor checkpoint differs')
        values={k:v for k,v in row.checkpoint['channel_values'].items() if k in GenState.__annotations__}
        saver=MemorySaver(serde=checkpoint_serde());graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=saver)
        config={'configurable':{'thread_id':cap.session_id}}
        graph.update_state(config,values,as_node={'architecture':'gate_architecture','specs':'items','tests':'tests'}[basis['phase']])
        authority();state=graph.get_state(config)
        if state.next!=(NEXT[basis['phase']][1],):raise ValueError('successor checkpoint schedule differs')
        receipt={'checkpoint':state.config,'snapshot':snapshots._retain_prepared(saver,config,purpose='approved-successor'),
            'review_sha256':review_sha256,'source_checkpoint_sha256':basis['checkpoint_sha256'],'approval_reference':basis['approval_reference'],
            'dispatch_started':False,'provider_invoked':False}
        authority();store.retain_decision_review(task_id,marker,review_sha256,receipt)
        return receipt
