"""Native PG evidence cannot route through the SQLite bridge/parent."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from delivery_api.tenancy import tenant_context
from vmodel_contracts.progress_signal import ProgressSignal


def test_event_progress_audit_and_diagnostic_native_boundary(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    from delivery_api.store import SQLiteStore
    with pg_workspace_factory() as workspace, tenant_context(workspace.scope):
        store = workspace.store
        task_id = store.create_task('Native evidence')['task_id']
        def forbidden(*args,**kwargs):
            raise AssertionError('evidence still uses SQLite compatibility path')
        monkeypatch.setattr(_Connection,'execute',forbidden)
        for name in ('add_event','events','progress_cursor','ingest_progress','audit','audit_entries',
                     '_ensure_waiting_input_locked','_validated_diagnostics_block','failure_diagnostics'):
            monkeypatch.setattr(SQLiteStore,name,forbidden)
        event = store.add_event(task_id,'test','Native evidence',payload={'retained':True})
        assert store.events(task_id)[-1]['id'] == event
        audit = store.audit('tester','native.checked',task_id,{'kept':True})
        assert store.audit_entries(task_id)[0]['id'] == audit
        waiting = store.ensure_waiting_input_event(task_id,'Question',phase_key='intent',payload={'input_id':'one'})
        assert store.ensure_waiting_input_event(task_id,'Question',phase_key='intent',payload={'input_id':'one'}) == waiting
        signal = ProgressSignal(event_id=str(uuid4()),cursor=1,task_id=task_id,session_id=str(uuid4()),
            phase_key='intent',stage='test',label='Working',status='started')
        assert store.ingest_progress(signal,'interview') is True
        assert store.ingest_progress(signal,'interview') is False
        assert store.progress_cursor(task_id,'interview',signal.session_id) == 1
        block = dict(error_class='TestFailure',role='delivery_app',sub_present=True,
                     session_id_present=True,session_active_for_sub=True,message='Safe failure')
        diag = store.record_failure_diagnostics(task_id,'test',503,block)
        assert store.failure_diagnostics(task_id)[0]['id'] == diag
        assert store.failure_diagnostics(task_id)[0]['block']['message'] == 'Safe failure'
        monkeypatch.undo()


def test_concurrent_progress_replay_commits_one_event(pg_workspace_factory):
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            task_id = workspace.store.create_task('Concurrent replay')['task_id']
        signal = ProgressSignal(event_id=str(uuid4()),cursor=1,task_id=task_id,session_id=str(uuid4()),
            phase_key='intent',stage='test',label='Working',status='started')
        import threading
        ready = threading.Barrier(2)
        def ingest():
            with tenant_context(workspace.scope):
                ready.wait(timeout=5)
                return workspace.store.ingest_progress(signal,'interview')
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _:ingest(),range(2)))
        assert sorted(outcomes) == [False,True]
        with tenant_context(workspace.scope):
            assert len([e for e in workspace.store.events(task_id) if e['kind']=='progress']) == 1


def test_progress_mid_write_failure_rolls_back_and_retries(pg_workspace_factory,monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store = workspace.store
        task_id = store.create_task('Atomic progress')['task_id']
        signal = ProgressSignal(event_id=str(uuid4()),cursor=1,task_id=task_id,session_id=str(uuid4()),
            phase_key='intent',stage='test',label='Working',status='started')
        original = PostgresStore._execute_native
        def fail_cursor(self,sql,parameters=(),**kwargs):
            if sql.startswith('INSERT INTO progress_ingest'):
                raise RuntimeError('cursor unavailable')
            return original(self,sql,parameters,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(PostgresStore,'_execute_native',fail_cursor)
            with pytest.raises(RuntimeError,match='cursor unavailable'):
                store.ingest_progress(signal,'interview')
        assert store.progress_cursor(task_id,'interview',signal.session_id) == 0
        assert store.events(task_id) == []
        assert store.ingest_progress(signal,'interview') is True
        assert store.progress_cursor(task_id,'interview',signal.session_id) == 1
        assert len(store.events(task_id)) == 1
