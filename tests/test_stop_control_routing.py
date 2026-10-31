"""A Stop epoch is not a closed-revision installation. Disposable PG only."""
import uuid
import pytest
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from delivery_api.pg_execution_fence import InstalledInterviewControlRouter


def test_normal_interview_stop_epoch_stays_on_normal_control_router(pg_scoped_workspace,tmp_path):
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.db_pool import pool_for
    ws=pg_scoped_workspace;s=ws.store;sid=str(uuid.uuid4())
    with tenant_context(ws.scope):
        tid=s.create_task('Stop epoch routing')['task_id'];s.set_engine_session(tid,sid)
        s.request_task_stop(tid)
        # The real engine Stop store advances the placement lease epoch.
        pg=PgSessionStore(pool_for(s._postgres.dsn),claims=(ws.scope.owner_id,ws.scope.session_id))
        pg.stop(sid,ws.scope.owner_id)
        assert s.session_binding(sid)['generation']==2
        assert s.installed_execution_anchor(sid) is None
        router=InstalledInterviewControlRouter(s,tmp_path)
        for name in ('snapshot','stop','reconcile_stopped','get'):
            assert router.resolve(name,sid) is None


def test_later_uninstalled_execution_does_not_get_stop_fallback(pg_scoped_workspace,tmp_path):
    ws=pg_scoped_workspace;s=ws.store;sid=str(uuid.uuid4())
    with tenant_context(ws.scope):
        tid=s.create_task('Uninstalled epoch')['task_id'];s.set_engine_session(tid,sid)
        with ws.admin() as conn:
            conn.execute('UPDATE engine_sessions SET generation=2 WHERE session_id=%s',(sid,))
            conn.execute('UPDATE tasks SET generation=2 WHERE task_id=%s',(tid,))
        with pytest.raises(TenantAccessDenied,match='binding unavailable'):
            InstalledInterviewControlRouter(s,tmp_path).resolve('snapshot',sid)
