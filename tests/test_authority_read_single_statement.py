"""WorkerCapability.validate reads task, session binding and auth-session liveness in ONE statement.

Merging the reads must not weaken the fence: each of these still denies, and the allowed case
returns the same task record the separate reads returned."""
import uuid
import pytest
from test_postgres_store import store,A,B,DSN
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from delivery_api.worker_capability import WorkerCapability


def _count(store,monkeypatch):
    calls=[];real=store._execute_native
    def counting(sql,parameters=(),**kw):
        calls.append(' '.join(sql.split())[:60]);return real(sql,parameters,**kw)
    monkeypatch.setattr(store,'_execute_native',counting)
    return calls


def _captured(store):
    tid=store.create_task('auth1-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'auth1-'+uuid.uuid4().hex)
    return tid,WorkerCapability.capture(store,tid)


def test_validate_is_one_statement_and_returns_the_task(store,monkeypatch):
    with tenant_context(A):
        tid,cap=_captured(store)
        calls=_count(store,monkeypatch)
        task=cap.validate(store)
        assert task['task_id']==tid and task['generation']==cap.generation and 'gates' in task and 'phases' in task
        assert len(calls)==1,calls


def test_validate_denies_after_stop_generation_bump(store):
    with tenant_context(A):
        tid,cap=_captured(store)
        store.request_task_stop(tid)
        with pytest.raises(TenantAccessDenied):cap.validate(store)


def test_validate_denies_deleted_task(store):
    with tenant_context(A):
        tid,cap=_captured(store)
        with store._postgres.transaction() as conn:conn.execute('UPDATE tasks SET deleted_at=1 WHERE task_id=%s',(tid,))
        with pytest.raises(TenantAccessDenied):cap.validate(store)


def test_validate_denies_missing_or_rebound_session(store):
    with tenant_context(A):
        tid,cap=_captured(store)
        with store._postgres.transaction() as conn:conn.execute('UPDATE engine_sessions SET generation=generation+1 WHERE session_id=%s',(cap.session_id,))
        with pytest.raises(TenantAccessDenied):cap.validate(store)
    with tenant_context(A):
        tid,cap=_captured(store)
        with store._postgres.transaction() as conn:conn.execute('DELETE FROM engine_sessions WHERE session_id=%s',(cap.session_id,))
        with pytest.raises(TenantAccessDenied):cap.validate(store)


def test_validate_denies_other_scope(store):
    with tenant_context(A):
        tid,cap=_captured(store)
    with tenant_context(B):
        with pytest.raises(TenantAccessDenied):cap.validate(store)


def test_validate_denies_revoked_auth_session(store):
    with tenant_context(A):
        tid,cap=_captured(store)
        cap.validate(store)
        import psycopg
        with psycopg.connect(DSN) as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
        with pytest.raises(TenantAccessDenied):cap.validate(store)
