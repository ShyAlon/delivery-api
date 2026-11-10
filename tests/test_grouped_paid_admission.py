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


def _unused_measure_current_shape(ledger):
    s, tid, job = ledger
    attempt = uuid.uuid4().hex
    with tenant_context(A):
        _, a, st1 = _syncs(lambda: s.reserve_provider_attempt(job, attempt, Q, 'digest'))
        _, b, st2 = _syncs(lambda: s.transition_provider_attempt(attempt, 'start'))
        _, c, st3 = _syncs(lambda: s.transition_provider_attempt(attempt, 'settle', {'input_tokens': 2, 'output_tokens': 3}))
    print('SYNCS reserve/start/settle', a, b, c)
    for st in (st1, st2, st3):
        print([e[:2] for e in st.trace])


def test_measure_admit_and_settle(ledger):
    s, tid, job = ledger
    attempt = uuid.uuid4().hex
    with tenant_context(A):
        r, a, st = _syncs(lambda: s.admit_provider_attempt(job, attempt, Q, 'digest', balance=None, bind_attempt=lambda x: None,
                                                          authorize=lambda: None, require_prepaid=False, launch_guard=False))
        assert r == 'allowed'
        _, b, st2 = _syncs(lambda: s.transition_provider_attempt(attempt, 'settle', {'input_tokens': 2, 'output_tokens': 3}))
    print('SYNCS admit/settle', a, b)
    # main: 25 / 11. A lock the transaction already holds is not asked for again (WIP target, lowered as grouping lands).
    assert a <= 21 and b <= 9, (a, b)
