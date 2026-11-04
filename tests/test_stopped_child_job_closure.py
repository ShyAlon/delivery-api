"""Stopped-generation RLS exception is terminal and provenance-preserving only."""
import uuid
import psycopg
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B,DSN,store


def test_stopped_original_owner_can_only_close_unchanged_old_job(store):
    from interview_engine.pg_session_store import PgSessionStore
    from delivery_api.db_pool import pool_for
    with tenant_context(A):
        tid=store.create_task('stopped RLS '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4())
        store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:intent');store.claim_worker(job)
        store.request_task_stop(tid)
        pg=PgSessionStore(pool_for(store._postgres.dsn),claims=(A.owner_id,A.session_id))
        pg.stop(sid,A.owner_id)
        old=store.worker_job(job)
        assert old['generation']==1 and store.session_binding(sid)['generation']==2
        for assignment in ("state='paused'", "state='running'", "state='failed'", "state='done',generation=2",
                           "state='done',attempt=attempt+1", "state='done',kind='changed'",
                           "state='done',auth_session_id='00000000-0000-0000-0000-0000000000a2'",
                           "state='done',execution_options='{}'::jsonb || '{\"mode\":\"fake\"}'::jsonb"):
            with pytest.raises(psycopg.Error):
                with store._postgres.transaction() as conn:
                    conn.execute('UPDATE worker_jobs SET '+assignment+' WHERE job_id=%s',(job,))
        # A generation mismatch without the durable engine Stop is not enough.
        with psycopg.connect(DSN) as admin:admin.execute("UPDATE engine_sessions SET control='{}' WHERE session_id=%s",(sid,))
        with pytest.raises(psycopg.Error):store.finish_stopped_worker(job,worker_quiescent=True)
        with psycopg.connect(DSN) as admin:admin.execute("UPDATE engine_sessions SET control='{\"stopped\":true}' WHERE session_id=%s",(sid,))
    with tenant_context(B):
        assert store.worker_job(job) is None
        with store._postgres.transaction() as conn:
            assert conn.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s RETURNING job_id",(job,)).fetchone() is None
    with tenant_context(A):
        # A second live auth session for the SAME owner is still not the original.
        from delivery_api.tenancy import TenantScope
        other=str(uuid.uuid4())
        with psycopg.connect(DSN) as admin:admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(other,A.owner_id))
        try:
            with tenant_context(TenantScope(A.owner_id,other)),pytest.raises(psycopg.Error):
                with store._postgres.transaction() as conn:conn.execute("UPDATE worker_jobs SET state='done' WHERE job_id=%s",(job,))
        finally:
            with psycopg.connect(DSN) as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(other,))
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE engine_sessions SET generation=3 WHERE session_id=%s',(sid,))
        with pytest.raises(psycopg.Error):store.finish_stopped_worker(job,worker_quiescent=True)
        with psycopg.connect(DSN) as admin:admin.execute('UPDATE engine_sessions SET generation=2 WHERE session_id=%s',(sid,))
        store.finish_stopped_worker(job,worker_quiescent=True)
        new=store.worker_job(job)
        assert new['state']=='done'
        assert {k:v for k,v in old.items() if k not in ('state','updated_at')}=={k:v for k,v in new.items() if k not in ('state','updated_at')}
        # No forged terminal INSERT into the old generation.
        with pytest.raises(psycopg.Error):
            with store._postgres.transaction() as conn:
                conn.execute("INSERT INTO worker_jobs SELECT %s,owner_id,auth_session_id,task_id,session_id,generation,kind,'done',attempt,created_at,updated_at,execution_options FROM worker_jobs WHERE job_id=%s",(uuid.uuid4().hex,job))
