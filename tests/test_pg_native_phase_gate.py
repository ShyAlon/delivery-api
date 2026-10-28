"""Native phase/gate/session operations preserve fences, not SQLite delegation."""
import pytest
from delivery_api.tenancy import tenant_context


def test_native_phase_gate_and_session_boundary(pg_workspace_factory,monkeypatch):
    from delivery_api.postgres_store import _Connection
    from delivery_api.store import SQLiteStore
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        s=workspace.store;tid=s.create_task('Native phase gate')['task_id']
        bridge=_Connection.execute
        def reject_family(self,sql,parameters=()):
            if not sql.startswith('\nSELECT t.*') and (any(table in sql for table in ('phases','gates')) or sql.startswith(('UPDATE tasks SET model_','SELECT engine_session_id','SELECT interview_schema_'))):
                raise AssertionError('phase/gate/session still uses SQLite bridge')
            return bridge(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject_family)
            for name in ('configure_simulation','set_gate_open','wait_at_gate','decide_gate','update_phase',
                'reconcile_completed_interview','claim_failed_needs_retry','claim_next_phase','claim_failed_phase_retry',
                'set_model_route','set_model_default','begin_interview_start','engine_session','bind_interview_schema',
                'interview_schema_binding','rerun_prerequisite'):
                patch.setattr(SQLiteStore,name,lambda *a,**k:(_ for _ in ()).throw(AssertionError('SQLite parent called')))
            s.set_model_route(tid,'ollama:test');s.set_model_default(tid,'test-model')
            assert s.begin_interview_start(tid,'native-session','ollama:test') is True
            assert s.begin_interview_start(tid,'other-session','ollama:test') is False
            assert s.engine_session(tid)=='native-session'
            assert s.bind_interview_schema(tid,'schema','1')['schema_id']=='schema'
            assert s.interview_schema_binding(tid)['schema_version']=='1'
            with pytest.raises(ValueError,match='immutable'):s.bind_interview_schema(tid,'other','2')
            s.update_phase(tid,'intent','awaiting_approval',detail='Ready',completed=1,total=1)
            gate=s.wait_at_gate(tid,'intent')
            with pytest.raises(ValueError,match='gate changed'):
                s.decide_gate(tid,gate['gate_key'],'approve',selected_option='Approve',expected_revision=gate['revision']-1)
            s.decide_gate(tid,gate['gate_key'],'approve',selected_option='Approve',expected_revision=gate['revision'])
            assert s.rerun_prerequisite(tid,'conops') is None
            assert s.rerun_prerequisite(tid,'needs')['phase_key']=='conops'
            from delivery_api.tenancy import TenantAccessDenied
            with pytest.raises(TenantAccessDenied):s.prepare_rerun(tid,'intent')


def test_concurrent_next_phase_claim_has_one_winner(pg_workspace_factory):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    with pg_workspace_factory() as w:
        with tenant_context(w.scope):
            s=w.store;tid=s.create_task('One next claim')['task_id'];s.set_engine_session(tid,'next-claim-session')
            s.update_phase(tid,'intent','approved');s.update_phase(tid,'conops','running');s.update_phase(tid,'conops','approved')
        ready=threading.Barrier(2)
        def claim(_):
            with tenant_context(w.scope):
                ready.wait(timeout=5)
                return s.claim_next_phase(tid,'needs','tester')
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,range(2)))
        assert sorted(results)==[False,True]
        with tenant_context(w.scope):
            assert len([e for e in s.audit_entries(tid) if e['action']=='phase.start'])==1


def test_gate_audit_failure_rolls_back_phase_and_revision(pg_workspace_factory,monkeypatch):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store;tid=s.create_task('Atomic gate')['task_id'];gate=s.wait_at_gate(tid,'intent')
        before=s.task(tid);audit=s.audit_entries(tid)
        original=s.audit
        def fail(actor,action,*args,**kwargs):
            if action=='gate.approve':raise RuntimeError('audit unavailable')
            return original(actor,action,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(s,'audit',fail)
            with pytest.raises(RuntimeError,match='audit unavailable'):
                s.decide_gate(tid,gate['gate_key'],'approve',selected_option='Approve',expected_revision=gate['revision'])
        assert s.task(tid)==before
        assert s.audit_entries(tid)==audit
