"""Immutable PG continuation preparation. No model/provider work or dispatch."""
from pathlib import Path
from types import SimpleNamespace
from .generator_snapshot import GeneratorSnapshots
from .pg_revision_stage import digest


def prepare(store,task_id,*,installation_id,artifacts_base):
    from spec_generator.cooperative_run import cli_stop_binding
    from spec_generator.stop_fence import check_execution
    from spec_generator.graph import build_graph,checkpoint_serde
    from spec_generator.revision_checkpoint import prepare_approved_continuation_checkpoint
    from langgraph.checkpoint.memory import MemorySaver
    from .revision_checkpoint_reader import read_source_checkpoint
    from .tenancy import session_root
    marker='revision-continuation:'+installation_id
    with store._task_operation_guard('task.rerun'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
        cap=basis['capability'];binding=cli_stop_binding();root=session_root(Path(artifacts_base),cap.scope,cap.session_id)
        if (binding is None or binding[1]!=cap.generation or getattr(binding[0],'_execution_anchor',None)!=cap.execution_anchor
            or Path(binding[0].path)!=root/'interview/stop-request.json'):
            raise ValueError('exact installed writer fence required')
        def authority():
            check_execution();cap.validate(store)
            if store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base)!=basis:
                raise ValueError('installed approval basis changed')
        authority()
        if store.decision_review(task_id,marker,basis['review_sha256']):raise ValueError('continuation already prepared or ambiguous; explicit reconciliation required')
        source=read_source_checkpoint(store,cap,None,session_id=cap.session_id,expected_checkpoint_id=basis['checkpoint_id'],expected_review_phase=basis['phase'])
        store.retain_decision_review(task_id,marker,basis['review_sha256'],{'state':'preparation_pending','checkpoint_id':basis['checkpoint_id'],
            'approval_reference':basis['approval_reference'],'dispatch_started':False})
    # Commit intent first. Any later fault is held, never replayed automatically.
    with store._task_operation_guard('task.rerun'):
        authority()
        saver=MemorySaver(serde=checkpoint_serde());graph=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=saver)
        cfg={'configurable':{'thread_id':cap.session_id}}
        receipt=prepare_approved_continuation_checkpoint(installed_state=source,execution_anchor=cap.execution_anchor,
            phase=basis['phase'],artifact_sha256=basis['artifact_sha256'],approval_reference=basis['approval_reference'],
            graph=graph,config=cfg,validate_authority=authority)
        snapshot=GeneratorSnapshots(store,cap)._retain_prepared(saver,cfg,purpose='approved-continuation')
        receipt=receipt|{'source_checkpoint_id':basis['checkpoint_id'],'review_sha256':basis['review_sha256'],'snapshot':snapshot}
        authority()
        store.retain_decision_review(task_id,marker+':prepared',basis['review_sha256'],receipt)
        return receipt


def reconcile(store,task_id,*,installation_id,artifacts_base,expected_job_id=None):
    from spec_generator.graph import build_graph
    with store._task_operation_guard('task.rerun'):
        basis=store.private_approved_continuation_basis(task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
        marker='revision-continuation:'+installation_id
        pending=store.decision_review(task_id,marker,basis['review_sha256'])
        prepared=store.decision_review(task_id,marker+':prepared',basis['review_sha256'])
        result={'state':'continuation_requires_review','automatic_retry':False,'automatic_cleanup':False,'dispatch_available':False}
        if not pending:return result|{'unverified':['preparation_intent_missing']}
        try:
            if pending!={'state':'preparation_pending','checkpoint_id':basis['checkpoint_id'],'approval_reference':basis['approval_reference'],'dispatch_started':False}:
                raise ValueError('continuation intent differs')
            if (not prepared or prepared['source_checkpoint_id']!=basis['checkpoint_id'] or prepared['review_sha256']!=basis['review_sha256']
                or prepared['approval_reference']!=basis['approval_reference'] or prepared['artifact_sha256']!=basis['artifact_sha256']
                or prepared['execution_session_id']!=basis['capability'].session_id or prepared['dispatch_started'] is not False or prepared['provider_invoked'] is not False):
                raise ValueError('continuation approval differs')
            reader=GeneratorSnapshots(store,basis['capability']).reader(prepared['snapshot'])
            state=build_graph(SimpleNamespace(record=lambda *a:None),review=True,checkpointer=reader).get_state({'configurable':{'thread_id':basis['capability'].session_id}})
            if state.next!=(prepared['next_node'],) or state.config!=prepared['checkpoint'] or digest(state.values[basis['phase']])!=basis['artifact_sha256']:
                raise ValueError('continuation schedule or typed artifact differs')
        except (ValueError,KeyError,TypeError):return result|{'unverified':['continuation_pg_evidence_unverified']}
        return result|{'state':'continuation_prepared_verified_no_dispatch','unverified':[],'next_node':prepared['next_node']}
