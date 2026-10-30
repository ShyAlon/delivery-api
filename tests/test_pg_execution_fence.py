"""PG publication/Stop races and accepted-call lifetime, no marker files."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from test_postgres_store import store,A,B
import pytest
from delivery_api.tenancy import tenant_context,session_root,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability
from delivery_api.pg_execution_fence import PostgresExecutionFence


def test_pg_fence_checks_scope_stop_generation_and_cross_process_writer(store,tmp_path):
    from spec_generator.stop_fence import StopRequested
    with tenant_context(A):
        tid=store.create_task('fence-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'fence-'+uuid.uuid4().hex)
        cap=WorkerCapability.capture(store,tid);root=session_root(tmp_path,A,cap.session_id)
        fence=PostgresExecutionFence(store,cap,root=root)
        with fence.running():
            with fence.marker_guard():fence.check(1)
            # A separate backend connection cannot claim the same writer.
            import psycopg
            from test_postgres_store import DSN
            key='pg-execution-writer:'+A.owner_id+':'+cap.session_id
            with psycopg.connect(DSN,autocommit=True) as conn:
                assert not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS held',(key,)).fetchone()[0]
            with tenant_context(B):
                with pytest.raises(TenantAccessDenied):fence.check(1)
        assert fence.active==0 and fence._lease is None and not root.exists()
        with pytest.raises(StopRequested):fence.check(2)
        with store._postgres.transaction() as conn:conn.execute('UPDATE tasks SET stop_requested_at=123 WHERE task_id=%s',(tid,))
        with pytest.raises(StopRequested):
            with fence.running():pass
        assert not root.exists()


def test_pg_fence_retains_writer_for_accepted_deadline_thread(store,tmp_path):
    from spec_generator.stop_fence import track_inflight_worker
    with tenant_context(A):
        tid=store.create_task('fence-inflight-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'fence-'+uuid.uuid4().hex)
        cap=WorkerCapability.capture(store,tid);root=session_root(tmp_path,A,cap.session_id)
        fence=PostgresExecutionFence(store,cap,root=root)
        with fence.running():release=track_inflight_worker();assert fence.active==2
        assert fence.active==1 and fence._lease is not None
        with ThreadPoolExecutor(1) as executor:executor.submit(release).result()
        assert fence.active==0 and fence._lease is None and not root.exists()


def test_pg_control_stop_is_idempotent_and_waits_for_surviving_writer(store,tmp_path):
    from delivery_api.pg_execution_fence import PostgresControlFence
    with tenant_context(A):
        tid=store.create_task('control-stop-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'control-'+uuid.uuid4().hex)
        cap=WorkerCapability.capture(store,tid);root=session_root(tmp_path,A,cap.session_id)
        writer=PostgresExecutionFence(store,cap,root=root);control=PostgresControlFence(store,cap)
        with writer.running():
            assert control.request_stop()=='STOP_REQUESTED'
            assert control.request_stop()=='STOP_REQUESTED' and store.task(tid)['generation']==2
            with pytest.raises(Exception,match='quiescence'):
                control.confirm_quiescent_after_recovery(no_live_worker=True)
        assert control.confirm_quiescent_after_recovery(no_live_worker=True)=='STOPPED'
        assert store.task(tid)['generation']==2 and not root.exists()
        with tenant_context(B):
            with pytest.raises(TenantAccessDenied):control.request_stop()
