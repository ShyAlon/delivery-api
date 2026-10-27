"""Per-request cost decomposition (2026-10-26 latency incident).

Polls took 12-14s with 1.5s of DB time while the engine was mid-turn, and the
request log could not say where the rest went. The request now carries its
engine-call time, pool checkout wait, auth time and transaction-held time in
the activity line and the Server-Timing header, the stream loop reports the
time it blocks the event loop on DB reads, and a loop-lag monitor names event
loop stalls. Measurement only: no behavior change."""
import time

import httpx
import pytest

from delivery_api import postgres_scope
from delivery_api.postgres_scope import (RequestStats, ScopedPostgres, note_auth,
    note_checkout, note_engine, request_stats)
from vmodel_contracts.interactive import EngineSnapshot, NextStep, ProcessStatus


def test_header_carries_engine_checkout_auth_and_held():
    stats = RequestStats()
    stats.queries = 3; stats.db_seconds = 0.4
    stats.engine_calls = 2; stats.engine_seconds = 1.25
    stats.checkout_seconds = 0.11; stats.auth_seconds = 0.07; stats.tx_held_seconds = 2.5
    header = stats.header()
    assert 'db;dur=400' in header
    assert 'engine;dur=1250' in header and '2 engine calls' in header
    assert 'checkout;dur=110' in header
    assert 'auth;dur=70' in header
    assert 'held;dur=2500' in header


def test_notes_accumulate_and_noop_without_request_context():
    note_engine(0.1); note_auth(0.1); note_checkout(0.1)  # no request context: never raises
    token = request_stats.set(RequestStats())
    try:
        note_engine(0.5); note_engine(0.25); note_auth(0.1); note_checkout(0.05)
        stats = request_stats.get()
        assert stats.engine_calls == 2 and abs(stats.engine_seconds - 0.75) < 1e-9
        assert abs(stats.auth_seconds - 0.1) < 1e-9 and abs(stats.checkout_seconds - 0.05) < 1e-9
    finally:
        request_stats.reset(token)


def _snapshot_body():
    snap = EngineSnapshot(session_id='00000000-0000-0000-0000-0000000000aa', task_id='t1',
        sequence=1, process_status=ProcessStatus.RUNNING, next_step=NextStep.RUN, open_input=None)
    return snap.model_dump(mode='json')


def test_engine_client_records_call_time_in_request_stats():
    from delivery_api.engine_client import HttpEngineClient
    def handler(request):
        time.sleep(0.02)
        return httpx.Response(200, json=_snapshot_body())
    client = HttpEngineClient('http://engine.test', token='x' * 24, transport=httpx.MockTransport(handler))
    token = request_stats.set(RequestStats())
    try:
        client.snapshot('00000000-0000-0000-0000-0000000000aa')
        stats = request_stats.get()
        assert stats.engine_calls == 1 and stats.engine_seconds >= 0.02
    finally:
        request_stats.reset(token)


def test_engine_call_outside_a_request_still_works():
    from delivery_api.engine_client import HttpEngineClient
    client = HttpEngineClient('http://engine.test', token='x' * 24,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_snapshot_body())))
    assert client.snapshot('00000000-0000-0000-0000-0000000000aa').session_id == '00000000-0000-0000-0000-0000000000aa'


def test_checkout_wait_is_timed():
    sp = object.__new__(ScopedPostgres); sp.opened = 0
    class FakePool:
        def getconn(self, timeout):
            time.sleep(0.05)
            return object()
    sp._pool = FakePool()
    token = request_stats.set(RequestStats())
    try:
        sp._checkout()
        assert request_stats.get().checkout_seconds >= 0.05
    finally:
        request_stats.reset(token)


def test_checkout_timeout_is_timed_before_raising():
    from psycopg_pool import PoolTimeout
    sp = object.__new__(ScopedPostgres); sp.opened = 0
    class BusyPool:
        def getconn(self, timeout):
            time.sleep(0.03)
            raise PoolTimeout('full')
    sp._pool = BusyPool()
    token = request_stats.set(RequestStats())
    try:
        with pytest.raises(TimeoutError):
            sp._checkout()
        assert request_stats.get().checkout_seconds >= 0.03
    finally:
        request_stats.reset(token)


def test_request_transaction_held_time_is_measured():
    token = request_stats.set(RequestStats())
    try:
        class FakePostgres:
            def _release(self, connection): pass
            def _drop(self, connection): pass
        class FakeTransaction:
            def __exit__(self, *args): return False
        handle = postgres_scope._RequestTransaction(FakePostgres(), scope=None)
        handle.connection = object(); handle.transaction = FakeTransaction()
        handle.first_begin = time.monotonic() - 1.2
        handle.finish(failed=False)
        assert request_stats.get().tx_held_seconds >= 1.2
    finally:
        request_stats.reset(token)


def test_stream_db_cycles_accumulate_loop_block_time():
    from delivery_api.app import stream_events_timed
    class FakeStore:
        def events(self, task_id, cursor=None):
            time.sleep(0.03)
            return []
    acc = {'cycles': 0, 'blocked_ms': 0, 'max_ms': 0}
    assert stream_events_timed(FakeStore(), 't1', 0, acc) == []
    assert acc['cycles'] == 1 and acc['blocked_ms'] >= 30 and acc['max_ms'] >= 30


def test_loop_lag_computation():
    from delivery_api.app import loop_lag_ms
    assert loop_lag_ms(100.0, 100.4) == 400
    assert loop_lag_ms(100.0, 99.9) == 0


def test_statements_are_labeled_and_timed_per_request():
    from delivery_api.postgres_scope import TimingConnection, note_statement
    note_statement('SELECT 1', 0.001)  # no request context: never raises

    class Fake:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params=()):
            self.calls.append(sql)
            return 'rows'
        def close(self):
            self.closed = True

    token = request_stats.set(RequestStats())
    try:
        conn = TimingConnection(Fake())
        assert conn.execute('SELECT   public.requi_session_active()   AS active') == 'rows'
        conn.execute('SELECT t.task_id,\n  t.title FROM tasks t')
        stats = request_stats.get()
        labels = [label for label, _ in stats.statements]
        assert labels == ['SELECT public.requi_session_active() AS active',
                          'SELECT t.task_id, t.title FROM tasks t']
        assert all(ms >= 0 for _, ms in stats.statements)
        top_label, top_ms = stats.top_statement()
        assert top_label in labels and top_ms >= 0
    finally:
        request_stats.reset(token)
