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
    # No paid-production guard bypass: fake quote intentionally isolates the
    # existing start transition from vendor network/launch policy in this test.
    quote=Q|{'provider':'deepseek','expense_units_per_usd':1,'retail_units_per_credit':10**9}
    attempt=uuid.uuid4().hex
    try:
        with tenant_context(A):
            s.reserve_provider_attempt(job,attempt,quote,'first')
            with pytest.raises(AdmissionDenied,match='explicit task'):s.transition_provider_attempt(attempt,'start')
            s.transition_provider_attempt(attempt,'cancel')
        with psycopg.connect(DSN) as c:
            c.execute('INSERT INTO preview_spend_approvals VALUES(%s,%s,1,%s,%s,%s,30,1,0)',(A.owner_id,tid,A.session_id,'deepseek',quote['model']))
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
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM preview_spend_approvals WHERE owner_id=%s',(A.owner_id,))


def test_approval_immutable_and_exact_owner_session(ledger):
    s,tid,job=ledger
    with psycopg.connect(DSN) as c:c.execute("UPDATE tasks SET state='not_started' WHERE task_id=%s",(tid,))
    try:
        with tenant_context(A):
            s.approve_preview_spend(tid,'deepseek-flash','0.10')
            s.approve_preview_spend(tid,'deepseek-flash','0.10')
            with pytest.raises(ValueError,match='immutable'):s.approve_preview_spend(tid,'deepseek-flash','0.20')
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM preview_spend_approvals WHERE owner_id=%s',(A.owner_id,))


def test_preview_route_selectable_is_not_approval(monkeypatch):
    from delivery_api import app as module,cloud_runtime
    from types import SimpleNamespace
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1');monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1');monkeypatch.setenv('DEEPSEEK_API_KEY','test-not-real')
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={'deepseek':'deepseek-flash','ollama':'phi4'}))
    monkeypatch.setattr(module.engine_client,'preview_route',lambda r:{'model_map':{'probing':[r,'deepseek-flash' if r=='deepseek' else 'phi4']}})
    routes=module.model_routes(a=None)['routes']
    assert all(r['available'] for r in routes)
    assert routes[0]['confirmation_required'] and not routes[1]['confirmation_required']


def test_concurrent_dispatch_cannot_double_use_ceiling(ledger,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    s,tid,job=ledger;monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    quote=Q|{'provider':'deepseek','expense_units_per_usd':1}
    with psycopg.connect(DSN) as c:c.execute('INSERT INTO preview_spend_approvals VALUES(%s,%s,1,%s,%s,%s,30,1,0)',(A.owner_id,tid,A.session_id,'deepseek',quote['model']))
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
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM preview_spend_approvals WHERE owner_id=%s',(A.owner_id,))


def test_estimate_uses_full_context_for_hard_cap(ledger,monkeypatch):
    s,tid,job=ledger;monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    quote=Q|{'provider':'deepseek','expense_units_per_usd':1,
        'estimate_policy':{'kind':'complete-wire-bytes-plus4096-v1','ceiling':100,'input_estimate':10,'hard_output_cap':10,'verified_input_bound':False}}
    try:
        with psycopg.connect(DSN) as c:c.execute('INSERT INTO preview_spend_approvals VALUES(%s,%s,1,%s,%s,%s,30,1,0)',(A.owner_id,tid,A.session_id,'deepseek',quote['model']))
        with tenant_context(A):
            attempt=uuid.uuid4().hex;s.reserve_provider_attempt(job,attempt,quote,'estimate')
            # Supply balance evidence only to isolate the later cap guard.
            from delivery_api.preview_spend import require
            from delivery_api.postgres_store import _transactional
            @_transactional
            def check(store):require(store,store.provider_attempt(attempt))
            with pytest.raises(AdmissionDenied,match='ceiling'):check(s)
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM preview_spend_approvals WHERE owner_id=%s',(A.owner_id,))


def test_http_approval_requires_exact_phrase_and_reviewed_model(monkeypatch):
    from delivery_api import app as module,cloud_runtime
    from types import SimpleNamespace
    from fastapi import HTTPException
    monkeypatch.setenv('DELIVERY_PAID_CHILD_PREVIEW','1')
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={'deepseek':'deepseek-flash'}))
    with pytest.raises(HTTPException) as exc:
        module.preview_spend_approval('fake',module.PreviewSpendApproval(model='deepseek-flash',ceiling_usd='1',confirmation='yes'),a=None)
    assert exc.value.status_code==422
    with pytest.raises(HTTPException) as exc:
        module.preview_spend_approval('fake',module.PreviewSpendApproval(model='other',ceiling_usd='1',confirmation='APPROVE USD 1'),a=None)
    assert exc.value.status_code==422
