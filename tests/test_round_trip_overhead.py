"""Per-call database overhead (2026-11-10 trace of one warm answer: 74% of round trips were overhead).

1. The pool's liveness check cost one network round trip on EVERY checkout. A connection the delivery
   path itself returned moments ago is not re-checked; one idle past the window still is. A server
   that killed a recent connection is still survived (the transaction start drops and replaces it).
   Other borrowers (the engine) keep the full check.
2. SAVEPOINT operation_start was its own round trip after BEGIN + identity. It now rides the same
   pipelined batch."""
import os
import uuid

import psycopg
import pytest

from delivery_api import db_pool, postgres_scope
from delivery_api.postgres_scope import ScopedPostgres
from delivery_api.tenancy import TenantScope, tenant_context

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
SCOPE = TenantScope('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1')


def _app_dsn():
    return DSN.replace('user=sandbox', 'user=delivery_app')


@pytest.fixture
def checks(monkeypatch):
    seen = []
    real = db_pool.ConnectionPool.check_connection
    monkeypatch.setattr(db_pool.ConnectionPool, 'check_connection', staticmethod(lambda c: (seen.append(1), real(c))[1]))
    return seen


def _use(store):
    with tenant_context(SCOPE), store.transaction() as connection:
        connection.execute('SELECT 1')


def test_recently_returned_delivery_connection_is_not_rechecked(checks):
    store = ScopedPostgres(_app_dsn())
    _use(store)                      # first checkout: new connection, checked once
    before = len(checks)
    for _ in range(5):_use(store)    # reused within the window: no round trip spent on checking
    assert len(checks) == before, 'a connection returned moments ago must not be re-checked'


def test_connection_idle_past_the_window_is_checked(checks, monkeypatch):
    store = ScopedPostgres(_app_dsn())
    _use(store)
    monkeypatch.setattr(db_pool, 'DELIVERY_RECHECK_AFTER_SECONDS', 0)
    before = len(checks)
    _use(store)
    assert len(checks) == before + 1


def test_engine_borrower_keeps_the_full_check(checks):
    pool = db_pool.pool_for(_app_dsn())
    for _ in range(3):
        conn = pool.getconn()
        pool.putconn(conn)
    assert len(checks) >= 3, 'engine-side checkouts keep psycopg_pool\'s check every time'


def test_server_killed_recent_connection_is_replaced_not_surfaced(checks):
    store = ScopedPostgres(_app_dsn())
    _use(store)
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='delivery_app' AND pid<>pg_backend_pid()")
    _use(store)                       # must succeed on a replacement connection


def test_savepoint_rides_the_pipelined_setup_batch(monkeypatch):
    from delivery_api.postgres_store import PostgresStore
    store = PostgresStore(_app_dsn())
    states = []
    real = psycopg.Connection.execute
    def spy(self, query, *args, **kwargs):
        if str(query).startswith('SAVEPOINT'):
            states.append(self.pgconn.pipeline_status)
        return real(self, query, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', spy)
    with tenant_context(SCOPE):
        task_id = store.create_task('rt-' + uuid.uuid4().hex)['task_id']      # a write call
        try:
            store.task(task_id)                                                # a read call
        finally:
            store.delete_task(task_id)
    assert states, 'no SAVEPOINT seen'
    assert all(s == psycopg.pq.PipelineStatus.ON for s in states), states


def test_failed_operation_still_rolls_back_to_the_savepoint():
    from delivery_api.postgres_store import PostgresStore
    store = PostgresStore(_app_dsn())
    with tenant_context(SCOPE):
        with pytest.raises(Exception):
            store.create_task(None)
        task_id = store.create_task('rb-' + uuid.uuid4().hex)['task_id']
        store.delete_task(task_id)
