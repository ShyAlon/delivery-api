"""Operator reservation-release audit read against the real Postgres: the policy,
the read-only role, and the back-office reflection of refusal releases."""
import uuid

import psycopg
import pytest

from test_postgres_store import DSN, A, B
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.operator_reads import OperatorReadStore
from delivery_api.tenancy import TenantAccessDenied, tenant_context

pytestmark = pytest.mark.skipif(not DSN, reason="disposable migrated Postgres required")
APP = DSN.replace("user=sandbox", "user=delivery_app") if DSN else None
CAPS = frozenset({"logs.read", "sessions.read"})
Q = {'provider': 'fake', 'model': 'fake', 'price_version': 'test-only', 'bound_version': 'test-only',
     'input_max': 10, 'output_max': 10, 'expense_input': 1, 'expense_output': 2, 'retail_input': 3, 'retail_output': 4,
     'retail_units_per_credit': 1000000}


@pytest.fixture
def releases(ledger):
    """One refusal release (real cancel path), one failed release still held,
    one failed release later resolved, one proof release, one unrelated row."""
    store, tid, job = ledger
    with tenant_context(A):
        refused = uuid.uuid4().hex
        store.reserve_provider_attempt(job, refused, Q, 'audit-read-refused')
        store.transition_provider_attempt(refused, 'cancel', {'refusal': 'positive prepaid balance required'})
        stuck = uuid.uuid4().hex
        store.reserve_provider_attempt(job, stuck, Q, 'audit-read-stuck')
        store.audit(str(A.owner_id), 'provider.attempt.release_failed', tid, {'attempt_id': stuck, 'error': 'RuntimeError'})
        resolved = uuid.uuid4().hex
        store.reserve_provider_attempt(job, resolved, Q, 'audit-read-resolved')
        store.audit(str(A.owner_id), 'provider.attempt.release_failed', tid, {'attempt_id': resolved, 'error': 'RuntimeError'})
        store.transition_provider_attempt(resolved, 'cancel', {'refusal': 'retry later succeeded'})
        proven = uuid.uuid4().hex
        store.audit(str(A.owner_id), 'provider.attempt.released', tid, {'attempt_id': proven, 'job_id': job, 'evidence': {}, 'expense_reserved': 30, 'billed_units': 0})
        store.audit(str(A.owner_id), 'task.renamed', tid, {'x': 1})
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
        c.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled) VALUES (%s,'ops-v1',%s,true) "
                  "ON CONFLICT (subject) DO UPDATE SET enabled=true,registry_version='ops-v1',capabilities=EXCLUDED.capabilities",
                  (A.owner_id, ["analytics.read", "sessions.read", "debug.read", "logs.read"]))
    yield store, tid, refused, stuck, resolved, proven
    with psycopg.connect(DSN) as c:
        c.execute("DELETE FROM public.operator_principals WHERE subject=%s", (A.owner_id,))


def read(store, owner=A, caps=CAPS, version="ops-v1"):
    reader = OperatorReadStore(APP, OperatorPrincipal(owner.owner_id, version, caps))
    with tenant_context(owner):
        return reader.attempt_releases(store)


def test_the_operator_sees_releases_with_reason_and_current_attention(releases):
    store, tid, refused, stuck, resolved, proven = releases
    out = read(store)
    mine = [r for r in out['releases'] if r['task_id'] == tid]   # the harness database is shared across files in one process
    by_attempt = {r['attempt_id']: r for r in mine}
    assert out['mutations_available'] is False and out['scope'] == 'reviewed_cross_user_operator_read'
    assert by_attempt[refused]['event'] == 'Reservation Released After Refusal'
    assert by_attempt[refused]['reason'] == 'positive prepaid balance required'
    assert by_attempt[refused]['current_state'] == 'Cancelled' and by_attempt[refused]['attention'] is False
    assert by_attempt[stuck]['event'] == 'Release Failed - Needs Attention' and by_attempt[stuck]['attention'] is True
    assert by_attempt[stuck]['reason'] == 'RuntimeError' and by_attempt[stuck]['current_state'] == 'Held'
    assert int(by_attempt[stuck]['expense_reserved']) > 0   # operator-only cost context, joined at read time
    assert by_attempt[resolved]['event'] == 'Release Failed - Since Resolved' and by_attempt[resolved]['attention'] is False
    assert by_attempt[proven]['event'] == 'Reservation Released With Proof'
    assert all(r['phase'] for r in mine)   # readable phase label; task titles stay outside the operator column bounds
    assert sum(1 for r in mine if r['attention']) == 1
    assert not any(r['event'] == 'task.renamed' for r in mine)


def test_the_operator_role_reads_only_curated_operations_actions_straight_from_the_database(releases):
    store, tid = releases[:2]
    with tenant_context(A):
        store.audit(str(A.owner_id), 'task.deleted', tid, {'accounting_retained': True})
        store.audit(str(A.owner_id), 'private.unreviewed', tid, {'secret': 'not operator data'})
    reader = OperatorReadStore(APP, OperatorPrincipal(A.owner_id, "ops-v1", CAPS))
    with tenant_context(A):
        with reader._read(store, "logs.read") as (connection, _authority):
            rows = connection.execute("SELECT DISTINCT action FROM public.audit_log").fetchall()
    actions = {r['action'] for r in rows}
    assert actions <= {'provider.attempt.uncertain', 'provider.attempt.cancelled',
                       'provider.attempt.released', 'provider.attempt.release_failed',
                       'task.deleted', 'task.operator_stop_requested', 'run.stopped',
                       'compute.segment_closed_no_provider_call', 'compute.segment_released_by_owner',
                       'compute.segment_reconciled_no_charge', 'audit.resolution'}
    assert 'task.deleted' in actions  # reviewed Audit-tab evidence is now visible
    assert 'task.renamed' not in actions and 'private.unreviewed' not in actions
    assert 'provider.attempt.cancelled' in actions   # non-vacuous: the new policy really widened the role


def test_a_user_who_is_not_the_registered_operator_is_denied_by_the_database(releases):
    store = releases[0]
    with pytest.raises(TenantAccessDenied): read(store, owner=B)
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE public.operator_principals SET enabled=false")
    with pytest.raises(TenantAccessDenied): read(store)
    with pytest.raises(TenantAccessDenied): read(store, caps=frozenset({"sessions.read"}))
