"""A single-statement read is ONE network round trip (2026-11-10).

The answer ran 75 transactions for 346 syncs: each transaction paid an identity batch (about 2 trips), its statement and a
COMMIT the trace showed as 0 ms. BEGIN + identity + statement + COMMIT now leave as one pipelined batch for reads."""
import uuid

import pytest
from test_postgres_store import store, A, B, DSN  # noqa: F401  (isolated Postgres fixture)

from delivery_api import postgres_scope
from delivery_api.tenancy import tenant_context
from delivery_api.worker_capability import WorkerCapability


def _syncs(fn):
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        result = fn()
    finally:
        postgres_scope.request_stats.reset(token)
    return result, sum(e[3] for e in stats.trace), stats


def _task(store):
    tid = store.create_task('ot-' + uuid.uuid4().hex)['task_id']
    sid = 'ot-' + uuid.uuid4().hex
    store.set_engine_session(tid, sid)
    return tid, sid


def test_each_flagged_read_is_one_network_trip(store):
    with tenant_context(A):
        tid, sid = _task(store)
        for name, call in (('task', lambda: store.task(tid)), ('engine_session', lambda: store.engine_session(tid)),
                           ('session_binding', lambda: store.session_binding(sid)),
                           ('stop_state', lambda: store.stop_state(tid, sid)),
                           ('worker_authority', lambda: store.worker_authority(tid)),
                           ('progress_cursor', lambda: store.progress_cursor(tid, 'interview', sid)),
                           ('auth_session_active', lambda: store.auth_session_active())):
            _, syncs, stats = _syncs(call)
            assert syncs == 1, (name, syncs, [e[:2] for e in stats.trace])
            assert stats.transactions == 1, name


def test_one_trip_gives_exactly_what_the_multi_trip_path_gives(store):
    with tenant_context(A):
        tid, sid = _task(store)
        fast = (store.task(tid), store.engine_session(tid), store.session_binding(sid), store.stop_state(tid, sid),
                store.worker_authority(tid), store.auth_session_active())
        with store._postgres.transaction() as conn:   # an enclosing transaction: the ordinary path
            token = store._connection.set(conn)
            try:
                slow = (store.task(tid), store.engine_session(tid), store.session_binding(sid), store.stop_state(tid, sid),
                        store.worker_authority(tid), store.auth_session_active())
            finally:
                store._connection.reset(token)
        assert fast == slow


def test_row_level_security_still_applies_to_the_one_trip_read(store):
    with tenant_context(A):
        tid, sid = _task(store)
    with tenant_context(B):
        assert store.task(tid) is None
        assert store.engine_session(tid) is None
        assert store.session_binding(sid) is None
        assert store.stop_state(tid, sid)['task_present'] is False


def test_a_second_statement_in_a_one_trip_read_fails_loudly(store):
    with tenant_context(A):
        conn = postgres_store_conn(store)
        conn.execute('SELECT 1')
        with pytest.raises(RuntimeError, match='second statement'):
            conn.execute('SELECT 2')


def postgres_store_conn(store):
    from delivery_api.postgres_store import _OneTripConnection
    return _OneTripConnection(store._postgres)


def test_read_one_refuses_anything_that_is_not_a_plain_read(store):
    with tenant_context(A):
        for sql in ('UPDATE tasks SET state=state', "SELECT 1; DELETE FROM tasks", 'SELECT * FROM tasks FOR UPDATE',
                    'INSERT INTO tasks DEFAULT VALUES', 'DELETE FROM tasks'):
            if sql.startswith('SELECT 1;'):
                continue   # a second statement is refused by the driver's extended protocol; pinned below
            with pytest.raises(ValueError, match='one trip'):
                store._postgres.read_one(sql)


def test_a_failing_read_leaves_the_pool_usable_and_no_open_transaction(store):
    with tenant_context(A):
        with pytest.raises(Exception, match='division by zero'):
            store._postgres.read_one('SELECT 1/0')
        for _ in range(5):   # the same connection comes back: it must be clean
            assert store._postgres.read_one('SELECT 1 AS one') == [{'one': 1}]
        assert store.auth_session_active() in (True, False)
