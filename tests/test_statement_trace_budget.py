"""The answer round-trip budget must be measurable from one debug ZIP (2026-11-10).

The ZIP logged the 30 most frequent (purpose, shape) rows: 175 of 449 entries. The rest could only be inferred, entries were
not network round trips (BEGIN + identity + SAVEPOINT leave as ONE pipelined batch but counted as three), and 129 entries were
'unattributed'. These tests pin: every purpose is logged, rows add up to the request total, pipelined entries cost 0 syncs,
and a phase summary line exists."""
import threading

import pytest

from delivery_api import observability, postgres_scope, request_costs, statement_trace


@pytest.fixture(autouse=True)
def _clean():
    request_costs.reset()
    yield
    request_costs.reset()


def _events(monkeypatch):
    seen = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **fields: seen.append((kind, fields)))
    return seen


def _entries(n_purposes=45, per_purpose=3):
    out = []
    for i in range(n_purposes):
        out.append(('app:purpose_%d' % i, 'SELECT set_config(?,?,true)', 300, 1))
        out.append(('app:purpose_%d' % i, 'SAVEPOINT operation_start', 0, 0))
        out.append(('app:purpose_%d' % i, 'COMMIT', 0, 1))
    return out


def test_every_purpose_is_logged_and_rows_add_up_to_the_request(monkeypatch):
    seen = _events(monkeypatch)
    stats = postgres_scope.RequestStats()
    stats.trace = _entries()                       # 135 entries over 45 purposes: the old 30-row cut hid 15 purposes
    statement_trace.fold_request('POST /api/tasks/<id>/answer', stats)
    rows = [f for k, f in seen if k == 'db.purpose_row']
    assert len(rows) == 45
    assert sum(r['entries'] for r in rows) == 135
    head = next(f for k, f in seen if k == 'db.statement_trace')
    assert head['round_trips'] == 135


def test_pipelined_entries_cost_no_network_trip(monkeypatch):
    seen = _events(monkeypatch)
    stats = postgres_scope.RequestStats()
    stats.trace = _entries()
    statement_trace.fold_request('POST /api/tasks/<id>/answer', stats)
    head = next(f for k, f in seen if k == 'db.statement_trace')
    assert head['network_syncs'] == 90 and head['transactions'] == 45   # 135 entries, 90 real syncs, 45 transactions
    rows = [f for k, f in seen if k == 'db.purpose_row']
    assert all(r['entries'] == 3 and r['syncs'] == 2 and r['transactions'] == 1 for r in rows)


def test_batched_savepoint_and_identity_are_recorded_with_their_real_cost():
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        postgres_scope.note_statement('SELECT set_config(?,?,true)', 0.3)
        postgres_scope.note_statement('SAVEPOINT operation_start', 0, 0)
        postgres_scope.note_statement('SELECT 1', 0.16)
    finally:
        postgres_scope.request_stats.reset(token)
    assert [e[3] for e in stats.trace] == [1, 0, 1]


def test_phase_budget_line_names_where_the_trips_went(monkeypatch):
    seen = _events(monkeypatch)
    stats = postgres_scope.RequestStats()
    stats.trace = ([('app:answer_interview<tenant_http:_serve', 'SELECT 1', 160, 1)] * 5
                   + [('postgres_store:task<worker_capability:capture<worker_capability:require_job', 'SELECT 1', 160, 1)] * 24
                   + [('stop_watch:watch_for_stop', 'SELECT 1', 160, 1)] * 6 + [('mystery', 'SELECT 1', 1, 1)] * 10)
    statement_trace.fold_request('POST /api/tasks/<id>/answer', stats)
    budget = next(f for k, f in seen if k == 'db.answer_budget')
    assert budget['network_syncs'] == 45 and budget['entries'] == 45
    assert budget['phase_handler'].startswith('5 entries, 5 syncs')
    assert budget['phase_authority'].startswith('24 entries, 24 syncs')
    assert budget['phase_watcher'].startswith('6 entries') and budget['phase_other'].startswith('10 entries')


def test_unattributed_statements_name_their_worker_thread():
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        import contextvars
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(statement_trace.record, 'statement', 'SELECT 1', 0.0), name='asyncio_3')
        t.start();t.join()
    finally:
        postgres_scope.request_stats.reset(token)
    # 2026-11-10: the label also names the calling code (see test_trace_names_every_caller)
    assert stats.trace[0][0].startswith('unattributed[thread=asyncio;from=')
