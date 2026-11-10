"""A paid call's ledger work is grouped, not one transaction per step (2026-11-10).

The debug ZIP's paid answer spent 117 syncs / 18 transactions on admission. Caps stay exact; only the transaction
boundaries and repeated locks/reads change."""
import uuid

import pytest
from test_attempt_ledger import ledger, Q  # noqa: F401
from test_postgres_store import A, DSN  # noqa: F401

from delivery_api import postgres_scope
from delivery_api.tenancy import tenant_context

pytestmark = pytest.mark.skipif(not DSN, reason='disposable Postgres required')


def _syncs(fn):
    stats = postgres_scope.RequestStats()
    token = postgres_scope.request_stats.set(stats)
    try:
        result = fn()
    finally:
        postgres_scope.request_stats.reset(token)
    return result, sum(e[3] for e in stats.trace), stats


def test_admit_and_settle_trip_budget_and_replay_does_not_charge_twice(ledger):
    s, tid, job = ledger
    attempt = uuid.uuid4().hex
    with tenant_context(A):
        r, a, st = _syncs(lambda: s.admit_provider_attempt(job, attempt, Q, 'digest', balance=None, bind_attempt=lambda x: None,
                                                          authorize=lambda: None, require_prepaid=False, launch_guard=False))
        assert r == 'allowed'
        _, b, st2 = _syncs(lambda: s.transition_provider_attempt(attempt, 'settle', {'input_tokens': 2, 'output_tokens': 3}))
    # main: 25 / 11. A lock the transaction already holds is not asked for again, authority is read once after both locks,
    # the insert returns its row, and transition reports its start state instead of a separate read.
    assert a <= 17 and b <= 8, (a, b)
    with tenant_context(A):
        first = s.provider_attempt(attempt)
        again = s.transition_provider_attempt(attempt, 'settle', {'input_tokens': 2, 'output_tokens': 3})
        assert again['state'] == 'settled' and again['expense_actual'] == first['expense_actual'] == 8
        assert again['retail_actual'] == first['retail_actual'] == 18 and again['settled_at'] == first['settled_at']


def test_a_held_advisory_lock_is_asked_for_once_and_asked_again_after_a_savepoint_rollback(ledger):
    s, tid, job = ledger
    lock = 'SELECT pg_advisory_xact_lock(73194720315621::bigint)'
    def twice():
        s._execute_native(lock); s._execute_native(lock)
    with tenant_context(A):
        _, syncs, _ = _syncs(lambda: s.run_grouped(twice))
        assert syncs == 3, syncs   # identity batch + one lock + commit; the second lock rode no trip
        def after_rollback():
            s._connection.get().execute('SAVEPOINT held_test')
            s._execute_native(lock)
            s._connection.get().execute('ROLLBACK TO SAVEPOINT held_test')
            s._execute_native(lock)
        _, syncs, _ = _syncs(lambda: s.run_grouped(after_rollback))
        assert syncs == 6, syncs  # identity + savepoint + lock + rollback + reacquire + commit
