"""One authority check (WorkerCapability.require_job) is ONE database read, not four.

It used to read task, session id and binding (capture) and then task, binding and sign-in again
(validate): four transactions at ~160 ms a trip each, six checks per answer. The fence is unchanged:
every denial below still denies with the same error."""
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




def _task(store):
    tid=store.create_task('rj1-'+uuid.uuid4().hex)['task_id'];store.set_engine_session(tid,'rj1-'+uuid.uuid4().hex)
    return tid,WorkerCapability.capture(store,tid)


def test_require_job_is_one_statement(store,monkeypatch):
    with tenant_context(A):
        tid,cap=_task(store)
        job={'task_id':tid,'generation':cap.generation,'session_id':cap.session_id,'owner_id':A.owner_id,'auth_session_id':A.session_id,'state':'running','attempt':1}
        calls=_count(store,monkeypatch)
        got=WorkerCapability.require_job(store,job)
        assert got.task_id==tid and got.session_id==cap.session_id and got.generation==cap.generation
        assert len(calls)==1,calls


def _denies(store,tid,cap,mutate,message=None):
    job={'task_id':tid,'generation':cap.generation,'session_id':cap.session_id,'owner_id':A.owner_id,'auth_session_id':A.session_id,'state':'running','attempt':1}
    mutate()
    with pytest.raises(TenantAccessDenied) as err:WorkerCapability.require_job(store,job)
    if message:assert message in str(err.value)


def test_require_job_still_denies_stop_rebind_delete_and_other_owner(store):
    with tenant_context(A):
        tid,cap=_task(store)
        _denies(store,tid,cap,lambda:store.request_task_stop(tid))
    with tenant_context(A):
        tid,cap=_task(store)
        def rebind():
            with store._postgres.transaction() as conn:conn.execute('UPDATE engine_sessions SET generation=generation+1 WHERE session_id=%s',(cap.session_id,))
        _denies(store,tid,cap,rebind,'current generation session binding required')
    with tenant_context(A):
        tid,cap=_task(store)
        def delete():
            with store._postgres.transaction() as conn:conn.execute('UPDATE tasks SET deleted_at=1 WHERE task_id=%s',(tid,))
        _denies(store,tid,cap,delete,'authorized worker task required')
    with tenant_context(A):
        tid,cap=_task(store)
    with tenant_context(B):
        _denies(store,tid,cap,lambda:None,'authorized worker task required')


def test_require_job_denies_a_job_for_another_session_or_missing_job(store):
    with tenant_context(A):
        tid,cap=_task(store)
        with pytest.raises(TenantAccessDenied):WorkerCapability.require_job(store,None)
        job={'task_id':tid,'generation':cap.generation,'session_id':'other','owner_id':A.owner_id,'auth_session_id':A.session_id,'state':'running','attempt':1}
        with pytest.raises(TenantAccessDenied):WorkerCapability.require_job(store,job)


def test_require_job_denies_a_revoked_sign_in(store):
    import psycopg
    with tenant_context(A):
        tid,cap=_task(store)
        job={'task_id':tid,'generation':cap.generation,'session_id':cap.session_id,'owner_id':A.owner_id,'auth_session_id':A.session_id,'state':'running','attempt':1}
        WorkerCapability.require_job(store,job)
        with psycopg.connect(DSN) as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
        try:
            with pytest.raises(TenantAccessDenied):WorkerCapability.require_job(store,job)
        finally:
            with psycopg.connect(DSN) as admin:admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',(A.session_id,A.owner_id))


def test_trace_names_who_asked_for_the_authority_check():
    from delivery_api import statement_trace
    import inspect
    assert "endswith(':require_job')" in inspect.getsource(statement_trace.purpose)
