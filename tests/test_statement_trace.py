"""Where the database round trips of one request went, by purpose (2026-11-10).

One warm answer ran 323-374 statements and nobody could say what they were FOR:
the debug ZIP kept only the slowest statement per request. The trace keeps a
bounded rolling count per (request type, calling purpose, statement shape),
including the per-call overhead (pool check, commit). Shapes have literals
removed, so the view is safe to share as is."""
import os
import uuid

import pytest

from delivery_api import postgres_scope, request_costs, statement_trace


@pytest.fixture(autouse=True)
def _clean():
    request_costs.reset()
    yield
    request_costs.reset()


def _rows(summary, type_):
    return {(r['purpose'], r['shape']): r['count'] for r in summary['types'][type_]}


def test_request_statements_fold_under_the_route_template():
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        for sql in ("SELECT * FROM tasks WHERE task_id='abc123def456'","SELECT * FROM tasks WHERE task_id='zzz'"):
            postgres_scope.note_statement(postgres_scope.statement_label(sql), 0.003)
        statement_trace.record('commit', 'COMMIT')
    finally:
        postgres_scope.request_stats.reset(token)
    request_costs.record_request('POST', '/api/tasks/7f3b86686274/answer', 200, 10, stats)
    rows = _rows(statement_trace.summary(), 'POST /api/tasks/<id>/answer')
    shapes = {shape: n for (_p, shape), n in rows.items()}
    assert shapes == {'SELECT * FROM tasks WHERE task_id=?': 2, 'COMMIT': 1}


def test_statements_outside_a_request_are_counted_as_background():
    postgres_scope.note_statement('SELECT 1', 0.001)
    summary = statement_trace.summary()
    assert _rows(summary, '(no request)')[(summary['types']['(no request)'][0]['purpose'], 'SELECT 1')] == 1


def test_trace_is_bounded():
    for i in range(statement_trace.MAX_KEYS + 50):
        postgres_scope.note_statement('SELECT col_%d FROM t' % i, 0.0)
    summary = statement_trace.summary(limit=10 ** 6)
    assert sum(len(v) for v in summary['types'].values()) <= statement_trace.MAX_KEYS
    assert summary['dropped'] >= 50


def test_summary_rides_the_request_cost_view_for_the_debug_zip():
    postgres_scope.note_statement('SELECT 2', 0.0)
    assert 'statement_trace' in request_costs.summary()


DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')


@pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
def test_a_store_call_records_its_overhead_and_its_caller():
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope, tenant_context
    app_dsn = DSN.replace('user=sandbox', 'user=delivery_app')
    scope = TenantScope('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1')
    store = PostgresStore(app_dsn)
    with tenant_context(scope):
        task_id = store.create_task('trace-' + uuid.uuid4().hex)['task_id']
        try:
            stats = postgres_scope.RequestStats()
            token = postgres_scope.request_stats.set(stats)
            try:
                store.task(task_id)
            finally:
                postgres_scope.request_stats.reset(token)
            assert sum(e[3] for e in stats.trace) == 1
            request_costs.record_request('GET', '/api/tasks/%s' % task_id, 200, 5, stats)
        finally:
            store.delete_task(task_id)
    rows = statement_trace.summary()['types']['GET /api/tasks/<id>']
    shapes = [r['shape'] for r in rows]
    assert any(s.startswith('SELECT set_config') for s in shapes), shapes
    # This plain read now commits in one pipeline, not a savepoint-wrapped write transaction.
    assert 'SAVEPOINT operation_start' not in shapes
    assert 'BEGIN READ ONLY [one trip]' in shapes
    assert 'COMMIT' in shapes
    assert any(s.startswith('SELECT t.*') for s in shapes)
    assert all('task' not in r['purpose'] or ':' in r['purpose'] for r in rows)
    assert task_id not in repr(rows)


def test_a_heavy_request_writes_its_own_purpose_breakdown_to_the_log(monkeypatch):
    """The debug ZIP carries logs, not the admin request-costs view, so a heavy request logs its breakdown itself."""
    from delivery_api import observability
    seen = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **fields: seen.append((kind, fields)))
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        for _ in range(60):
            postgres_scope.note_statement(postgres_scope.statement_label("SELECT * FROM tasks WHERE task_id='abc'"), 0.002)
        statement_trace.record('commit', 'COMMIT')
    finally:
        postgres_scope.request_stats.reset(token)
    request_costs.record_request('POST', '/api/tasks/7f3b86686274/answer', 200, 10, stats)
    # Header line plus one line per ranked row (a joined field was truncated by the log's 500-character cap).
    # The 2026-11-10 budget adds one line per purpose (not cut to 30 rows) and a per-phase summary at the end.
    assert [k for k, _ in seen] == ['db.statement_trace', 'db.statement_row', 'db.statement_row', 'db.purpose_row', 'db.answer_budget']
    f = seen[0][1]
    assert f['request_type'] == 'POST /api/tasks/<id>/answer' and f['round_trips'] == 61
    assert seen[1][1]['shape'] == 'SELECT * FROM tasks WHERE task_id=?' and seen[1][1]['count'] == 60 and '7f3b86686274' not in str(seen)


def test_a_light_request_writes_no_trace_line(monkeypatch):
    from delivery_api import observability
    seen = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **fields: seen.append(kind))
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        postgres_scope.note_statement('SELECT 1', 0.001)
    finally:
        postgres_scope.request_stats.reset(token)
    request_costs.record_request('GET', '/api/auth', 200, 5, stats)
    assert seen == []


def test_heavy_request_logs_every_ranked_row_untruncated(monkeypatch):
    """2026-11-10: one joined 'rows' field was cut at 500 characters, so an 845-round-trip answer showed 5 rows."""
    from delivery_api import observability, log_safety
    lines = []
    monkeypatch.setattr(observability, 'event', lambda kind, message, **fields: lines.append(observability._line(kind, message, fields)))
    stats = postgres_scope.RequestStats()
    stats.trace = [('postgres_store:purpose_%d<very_long_caller_chain<another_caller<http_service:progress' % (i % 12), 'SELECT col_a, col_b FROM table_%d WHERE x=?' % (i % 12), 3) for i in range(120)]
    request_costs.record_request('POST', '/api/tasks/7f3b86686274/answer', 200, 10, stats)
    text = '\n'.join(lines)
    assert '[truncated]' not in text, text
    for i in range(12):
        assert 'purpose_%d<' % i in text and 'table_%d ' % i in text
    assert text.count('db.statement_row') == 12
