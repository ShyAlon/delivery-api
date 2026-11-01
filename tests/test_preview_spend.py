"""Real migrated ledger guard, no paid transport. Frozen task approval and held exposure."""
import uuid
import psycopg
import pytest
from test_attempt_ledger import ledger,Q
from test_postgres_store import A,DSN
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.preview_spend import limit_units

@pytest.mark.parametrize('value',['0','-1','NaN','Infinity','0.0000000000001','999999999999999999999999'])
def test_invalid_ceiling(value):
    with pytest.raises(ValueError):limit_units(value)


def test_missing_approval_and_exact_cap_on_every_dispatch(ledger,monkeypatch):
    s,tid,job=ledger;monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_expense_policy_head; DELETE FROM operator_expense_policy_revisions')
    # No paid-production guard bypass: fake quote intentionally isolates the
    # existing start transition from vendor network/launch policy in this test.
    quote=Q|{'provider':'deepseek','expense_units_per_usd':10**12,'retail_units_per_credit':10**9}
    attempt=uuid.uuid4().hex
    try:
        with tenant_context(A):
            s.reserve_provider_attempt(job,attempt,quote,'first')
            with pytest.raises(AdmissionDenied,match='Operator expense policy missing'):s.transition_provider_attempt(attempt,'start')
            s.transition_provider_attempt(attempt,'cancel')
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES(%s,30,1000000000000,%s,'test policy')",('test-policy',A.owner_id));c.execute("INSERT INTO operator_expense_policy_head VALUES(true,'test-policy')")
        with tenant_context(A):
            first=uuid.uuid4().hex;s.reserve_provider_attempt(job,first,quote,'one');s.transition_provider_attempt(first,'start')
            second=uuid.uuid4().hex;s.reserve_provider_attempt(job,second,quote,'two')
            with pytest.raises(AdmissionDenied,match='ceiling'):s.transition_provider_attempt(second,'start')
            s.transition_provider_attempt(second,'cancel')
            s.transition_provider_attempt(first,'settle',{'input_tokens':2,'output_tokens':3})
            # Settled actual8 plus next worst-case30 still exceeds cap30.
            third=uuid.uuid4().hex;s.reserve_provider_attempt(job,third,quote,'three')
            with pytest.raises(AdmissionDenied,match='ceiling'):s.transition_provider_attempt(third,'start')
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_expense_policy_head; DELETE FROM operator_expense_policy_revisions')


def test_concurrent_dispatch_cannot_double_use_ceiling(ledger,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=ledger;monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    quote=Q|{'provider':'deepseek','expense_units_per_usd':10**12}
    with psycopg.connect(DSN) as c:c.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES(%s,30,1000000000000,%s,'test policy')",('test-policy',A.owner_id));c.execute("INSERT INTO operator_expense_policy_head VALUES(true,'test-policy')")
    def run(i):
        local=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
        with tenant_context(A):
            attempt=uuid.uuid4().hex
            try:
                # Same atomic admission operation used by the parent broker.
                result=local.admit_provider_attempt(job,attempt,quote,str(i),balance=None,bind_attempt=lambda a:None,authorize=lambda:None,require_prepaid=False,launch_guard=False)
                return result=='allowed'
            except AdmissionDenied:return False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(run,range(2)))==1
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_expense_policy_head; DELETE FROM operator_expense_policy_revisions')


def test_estimate_uses_full_context_for_hard_cap(ledger,monkeypatch):
    s,tid,job=ledger;monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    quote=Q|{'provider':'deepseek','expense_units_per_usd':10**12,
        'estimate_policy':{'kind':'complete-wire-bytes-plus4096-v1','ceiling':100,'input_estimate':10,'hard_output_cap':10,'verified_input_bound':False}}
    try:
        with psycopg.connect(DSN) as c:c.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES(%s,30,1000000000000,%s,'test policy')",('test-policy',A.owner_id));c.execute("INSERT INTO operator_expense_policy_head VALUES(true,'test-policy')")
        with tenant_context(A):
            attempt=uuid.uuid4().hex;s.reserve_provider_attempt(job,attempt,quote,'estimate')
            # Supply balance evidence only to isolate the later cap guard.
            from delivery_api.operator_expense_policy import require
            from delivery_api.postgres_store import _transactional
            @_transactional
            def check(store):require(store,store.provider_attempt(attempt))
            with pytest.raises(AdmissionDenied,match='ceiling'):check(s)
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_expense_policy_head; DELETE FROM operator_expense_policy_revisions')
