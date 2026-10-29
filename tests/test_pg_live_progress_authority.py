"""Observational engine progress under actual migrated FORCE RLS policies."""
import os
import sys
from uuid import uuid4
import psycopg
import pytest


def test_live_progress_real_auth_session_revocation_and_tenant_isolation(pg_scoped_workspace):
    source=os.environ.get('DELIVERY_TEST_ENGINE_SOURCE')
    if source:sys.path.insert(0,source)
    from interview_engine.pg_session_store import PgSessionStore
    from interview_engine.pg_journal import ObservationalJournal
    from interview_engine.stop_fence import StopRequested
    from delivery_api.db_pool import pool_for
    from delivery_api.tenancy import tenant_context
    import pg_harness
    scope=pg_scoped_workspace.scope
    with tenant_context(scope):task=pg_scoped_workspace.store.create_task('Live journal '+uuid4().hex,'brief')
    auth=str(uuid4());sid=str(uuid4())
    admin_dsn=pg_harness.STATE['dsn']
    with psycopg.connect(admin_dsn,autocommit=True) as admin:
        admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s)',(auth,scope.owner_id))
    pool=pool_for(pg_scoped_workspace.store._postgres.dsn)
    store=PgSessionStore(pool,claims=(scope.owner_id,auth))
    try:
        store.create(sid,task['task_id'],scope.owner_id)
        claim=store.claim(sid,scope.owner_id,'live-authority',ttl=60)
        journal=ObservationalJournal(store,claim,sid,task['task_id'])
        journal.append(phase_key='intent',stage='probe',label='Private',status='heartbeat')
        assert len(store.read_journal(sid,scope.owner_id,0,100))==1
        foreign=PgSessionStore(pool,claims=('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2'))
        assert foreign.read_journal(sid,scope.owner_id,0,100)==[]
        with pytest.raises(StopRequested):
            ObservationalJournal(foreign,claim,sid,task['task_id']).append(phase_key='intent',stage='probe',label='Forged',status='heartbeat')
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            admin.execute('DELETE FROM auth.sessions WHERE id=%s',(auth,))
        assert store.read_journal(sid,scope.owner_id,0,100)==[]
        with pytest.raises(StopRequested):journal.append(phase_key='intent',stage='probe',label='Revoked',status='heartbeat')
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            assert admin.execute('SELECT count(*) FROM engine_session_journal WHERE session_id=%s',(sid,)).fetchone()[0]==1
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            admin.execute('DELETE FROM engine_session_journal WHERE session_id=%s',(sid,))
            admin.execute('DELETE FROM engine_sessions WHERE session_id=%s',(sid,))
            admin.execute('DELETE FROM auth.sessions WHERE id=%s',(auth,))
