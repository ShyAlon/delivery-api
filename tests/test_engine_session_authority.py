"""Engine session authority: the placement RLS policy admits only REAL auth sessions.

Hermetic regression for the 2026-10-24 interview-start failure: the engine
connected with synthetic claims (session id = owner UUID) and the
engine_sessions policy (requi_session_active) rejected its first write with
SQLSTATE 42501. These tests drive the engine's REAL store path (PgSessionStore
over owner_scoped_dsn claims) on the throwaway migrated Postgres: synthetic
claims fail, real claims pass, a foreign session fails, and a deleted
(signed-out) session fails on an ALREADY-OPEN connection (the policy re-reads
auth.sessions on every statement). The seed keeps owner and session ids
distinct so this coverage can never go vacuous again.
"""
import os
import sys
from pathlib import Path

import psycopg
import pytest

import pg_harness

pytest.importorskip('pgserver', reason='pgserver not installed: authority tests skip')
ENGINE_SRC = os.environ.get('DELIVERY_TEST_ENGINE_SOURCE')
if ENGINE_SRC and (Path(ENGINE_SRC) / 'src/interview_engine/tenant_worker.py').is_file():
    sys.path.insert(0, str(Path(ENGINE_SRC) / 'src'))
tenant_worker = pytest.importorskip('interview_engine.tenant_worker', reason='interview-engine checkout required')

OWNER_A = '00000000-0000-0000-0000-000000000001'
OWNER_B = '00000000-0000-0000-0000-000000000002'
SESSION_A = '00000000-0000-0000-0000-0000000000a1'
SESSION_B = '00000000-0000-0000-0000-0000000000a2'


@pytest.fixture(scope='module')
def dsn():
    if not pg_harness.STATE.get('dsn'):
        pg_harness.start()
    if not pg_harness.STATE.get('dsn'):
        pytest.skip('pg_harness unavailable: ' + pg_harness.STATE.get('reason', ''))
    return pg_harness.STATE['dsn']


def _engine_store(dsn, sub, session_id):
    # The engine's real connection path: the delivery_app role (NOSUPERUSER
    # NOBYPASSRLS, inheriting requi_backend -> authenticated) with tenant
    # claims in the conninfo.
    dsn = dsn.replace('user=sandbox', 'user=delivery_app')
    return tenant_worker.pg_session_store(tenant_worker.owner_scoped_dsn(dsn, sub, session_id))


def _seed_task(dsn, tag):
    # engine_sessions references (owner_id, task_id) into tasks.
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES (%s,%s,'pending',0,0,%s) ON CONFLICT (task_id) DO NOTHING",
                      ('task-' + tag, 'title-' + tag, OWNER_A))


@pytest.fixture()
def tag(dsn):
    # Fresh identifiers per test: store.create is ON CONFLICT DO NOTHING, so a
    # leftover row from any earlier run would no-op instead of exercising the
    # policy. Cleanup runs as admin, never through the identity under test.
    import uuid
    value = uuid4hex = uuid.uuid4().hex[:12]
    yield value
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute('DELETE FROM engine_sessions WHERE session_id=%s', ('sess-' + value,))
        admin.execute('DELETE FROM tasks WHERE task_id=%s', ('task-' + value,))


def test_seed_keeps_session_ids_distinct_from_user_ids(dsn):
    # The invariant itself: if the seed ever reuses a user id as a session id,
    # synthetic engine claims validate and every test below goes vacuous.
    assert SESSION_A != OWNER_A and SESSION_B != OWNER_B
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute('SELECT id, user_id FROM auth.sessions').fetchall()
    assert rows and all(session != user for session, user in rows)


def test_engine_synthetic_claims_rejected(dsn, tag):
    # The exact pre-fix engine claims: session id = owner UUID.
    _seed_task(dsn, tag)
    store = _engine_store(dsn, OWNER_A, OWNER_A)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            store.create('sess-'+tag, 'task-'+tag, OWNER_A)
    finally:
        store.conn.close()


def test_engine_real_session_claims_accepted(dsn, tag):
    store = _engine_store(dsn, OWNER_A, SESSION_A)
    try:
        _seed_task(dsn, tag)
        store.create('sess-'+tag, 'task-'+tag, OWNER_A)
    finally:
        store.conn.close()


def test_engine_foreign_session_rejected(dsn, tag):
    # Owner A presenting owner B's live session: the policy binds session to subject.
    _seed_task(dsn, tag)
    store = _engine_store(dsn, OWNER_A, SESSION_B)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            store.create('sess-'+tag, 'task-'+tag, OWNER_A)
    finally:
        store.conn.close()


def test_signout_fails_next_statement_on_existing_connection(dsn, tag):
    store = _engine_store(dsn, OWNER_A, SESSION_A)
    try:
        _seed_task(dsn, tag)
        store.create('sess-'+tag, 'task-'+tag, OWNER_A)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute('DELETE FROM auth.sessions WHERE id=%s', (SESSION_A,))
        # Same open serving connection, next statement: the policy re-reads
        # auth.sessions, so sign-out takes effect without any reconnect.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _seed_task(dsn, tag)
            store.create('sess-'+tag, 'task-'+tag, OWNER_A)
    finally:
        store.conn.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING', (SESSION_A, OWNER_A))
