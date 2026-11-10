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
            request_costs.record_request('GET', '/api/tasks/%s' % task_id, 200, 5, stats)
        finally:
            store.delete_task(task_id)
    rows = statement_trace.summary()['types']['GET /api/tasks/<id>']
    shapes = [r['shape'] for r in rows]
    assert any(s.startswith('SELECT set_config') for s in shapes), shapes
    assert 'SAVEPOINT operation_start' in shapes
    assert 'COMMIT' in shapes
    assert any(s.startswith('SELECT t.*') for s in shapes)
    assert all('task' not in r['purpose'] or ':' in r['purpose'] for r in rows)
    assert task_id not in repr(rows)
