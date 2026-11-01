"""Task projection and stop/delete native SQL retain generation and accounting fences."""
import pytest
from uuid import uuid4
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from delivery_api.attempt_ledger import AdmissionDenied


def test_native_task_lifecycle_and_session_bindings(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    with pg_workspace_factory() as a,pg_workspace_factory() as b:
        s=a.store;original=_Connection.execute
        def reject(self,sql,parameters=()):
            if any(x in sql for x in (' tasks','engine_sessions','worker_jobs','provider_attempts','retail_compute_segments','retail_contributions','requi_session_active','DELETE FROM')):
                raise AssertionError('task lifecycle still consumes dialect bridge')
            return original(self,sql,parameters)
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            with tenant_context(a.scope):
                assert s.auth_session_active()
                tid=s.create_task('Native lifecycle')['task_id'];sid='lifecycle-'+uuid4().hex
                s.set_engine_session(tid,sid);s.set_engine_session(tid,sid)
                assert s.session_binding(sid)['task_id']==tid
                assert tid in [t['task_id'] for t in s.list_tasks()]
                job=s.enqueue_worker(tid,'fake');assert s.claim_worker(job)
                generation=s.task(tid)['generation']
                with pytest.raises(AdmissionDenied):s.confirm_task_stopped(tid,engine_quiescent=True)
                stopped=s.request_task_stop(tid)
                assert stopped['generation']==generation+1
                assert s.request_task_stop(tid)['generation']==generation+1
                with pytest.raises(ValueError,match='explicit confirmation'):s.delete_task(tid,confirm=False)
                assert s.task(tid) is not None
                with pytest.raises(AdmissionDenied,match='engine quiescence'):s.confirm_task_stopped(tid,engine_quiescent=False)
                with pytest.raises(AdmissionDenied,match='worker remains active'):s.confirm_task_stopped(tid,engine_quiescent=True)
                s.finish_stopped_worker(job,worker_quiescent=True)
                assert s.confirm_task_stopped(tid,engine_quiescent=True)['state']=='stopped'
                assert s.confirm_task_stopped(tid,engine_quiescent=True)['state']=='stopped'
                s.delete_task(tid)
                assert s.task(tid) is None
                assert tid not in [t['task_id'] for t in s.list_tasks()]
                assert s.worker_job(job)['state']=='done'
                assert s.session_binding(sid)['task_id']==tid
                plain=s.create_task('Plain deletion')['task_id'];s.delete_task(plain)
                assert s.task(plain) is None
                other=s.create_task('Other session')['task_id']
                with pytest.raises(TenantAccessDenied,match='immutable'):s.set_engine_session(other,sid)
            with tenant_context(b.scope):
                assert s.task(tid) is None
                assert s.session_binding(sid) is None
                with pytest.raises(KeyError):s.request_task_stop(tid)
                with pytest.raises(KeyError):s.delete_task(other)


def test_native_delete_postwrite_failure_preserves_task(pg_workspace_factory,monkeypatch):
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store;tid=s.create_task('Rollback delete')['task_id']
        original=s._execute_native
        def fail(sql,parameters=(),*,write=False):
            out=original(sql,parameters,write=write)
            if sql.startswith('UPDATE tasks SET deleted_at='):raise RuntimeError('after real hide')
            return out
        before=s.task(tid)
        with monkeypatch.context() as patch:
            patch.setattr(s,'_execute_native',fail)
            with pytest.raises(RuntimeError,match='after real hide'):s.delete_task(tid)
        assert s.task(tid)==before
