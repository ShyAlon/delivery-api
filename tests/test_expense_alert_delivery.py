"""Actual disposable outbox; fake transport, no email/network or installed sender."""
import uuid
import pytest
from delivery_api.expense_alert import prepare_alert
from delivery_api.expense_alert_delivery import ExpenseAlertTransport,deliver_alert
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied
from test_attempt_ledger import ledger,A,B,DSN


def queued(store,max_attempts=2):
    alert=prepare_alert(episode_id=uuid.uuid4().hex,scope='session',scope_id='fake-session',spent_units=3,limit_units=3,units_per_usd=1,occurred_at=1)
    store.enqueue_expense_alert(alert,max_attempts=max_attempts)
    return alert.key


@pytest.mark.parametrize('outcome,state',[('accepted','sent'),('transient_failure','retry_pending'),('permanent_failure','operator_review'),('unknown','operator_review'),('invalid','operator_review')])
def test_delivery_commits_claim_then_records_explicit_result(ledger,outcome,state):
    import psycopg
    store,_,_=ledger;calls=[]
    with tenant_context(A):
        key=queued(store)
        def send(identity,evidence):
            with psycopg.connect(DSN) as c:
                assert c.execute('SELECT state,attempts FROM expense_alert_outbox WHERE alert_key=%s',(key,)).fetchone()==('sending',1)
            calls.append((identity,evidence));return outcome
        transport=ExpenseAlertTransport('fixture-route',send)
        assert deliver_alert(store,key,transport)==state
        assert len(calls)==1 and calls[0][0]==key and set(calls[0][1])=={'episode_id','scope','scope_id','spent_units','limit_units','units_per_usd','occurred_at','action','running_calls'}
        if state!='retry_pending':assert deliver_alert(store,key,transport) is None and len(calls)==1


def test_unknown_acceptance_and_finish_crash_never_resend(ledger,monkeypatch):
    store,_,_=ledger;calls=[]
    with tenant_context(A):
        key=queued(store)
        transport=ExpenseAlertTransport('fixture-route',lambda *args:(calls.append(1),'accepted')[1])
        original=store.finish_expense_alert
        monkeypatch.setattr(store,'finish_expense_alert',lambda *a:(_ for _ in ()).throw(RuntimeError('crash')))
        with pytest.raises(RuntimeError):deliver_alert(store,key,transport)
        monkeypatch.setattr(store,'finish_expense_alert',original)
        assert deliver_alert(store,key,transport) is None
        store.recover_uncertain_expense_alert(key)
        assert deliver_alert(store,key,transport) is None and calls==[1]


def test_retry_budget_tenant_and_commit_boundary(ledger):
    store,_,_=ledger;calls=[];transport=ExpenseAlertTransport('fixture-route',lambda *a:(calls.append(1),'transient_failure')[1])
    with tenant_context(A):key=queued(store)
    with tenant_context(B),pytest.raises(KeyError):deliver_alert(store,key,transport)
    with tenant_context(A):
        with store._postgres.transaction() as conn:
            token=store._connection.set(conn)
            try:
                with pytest.raises(AdmissionDenied,match='commit boundary'):deliver_alert(store,key,transport)
            finally:store._connection.reset(token)
        assert calls==[]
        assert deliver_alert(store,key,transport)=='retry_pending'
        assert deliver_alert(store,key,transport)=='operator_review'
        assert deliver_alert(store,key,transport) is None and calls==[1,1]


def test_transport_exception_is_unknown_and_payload_cannot_route(ledger):
    store,_,_=ledger;calls=[]
    with tenant_context(A):
        key=queued(store)
        with store._postgres.transaction() as c:c.execute("UPDATE expense_alert_outbox SET payload=payload || %s::jsonb WHERE alert_key=%s",('{"to":"attacker@example.invalid","body":"not sent"}',key))
        def send(key,payload):
            calls.append(payload);assert 'to' not in payload and 'body' not in payload
            raise TimeoutError('acceptance lost')
        assert deliver_alert(store,key,ExpenseAlertTransport('fixture-route',send))=='operator_review'
        assert len(calls)==1


def test_request_transaction_cannot_defer_claim_commit(ledger):
    from delivery_api.postgres_scope import _request_tx
    store,_,_=ledger
    with tenant_context(A):
        key=queued(store);token=_request_tx.set(object())
        try:
            with pytest.raises(AdmissionDenied,match='commit boundary'):
                deliver_alert(store,key,ExpenseAlertTransport('fixture',lambda *a:pytest.fail('no send')))
        finally:_request_tx.reset(token)


def test_two_delivery_workers_send_only_once(ledger):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from delivery_api.postgres_store import PostgresStore
    store,_,_=ledger;entered=threading.Event();release=threading.Event();calls=[]
    with tenant_context(A):key=queued(store)
    def send(*a):calls.append(1);entered.set();assert release.wait(5);return 'accepted'
    transport=ExpenseAlertTransport('fixture-route',send)
    def run():
        with tenant_context(A):return deliver_alert(PostgresStore(DSN.replace('user=sandbox','user=delivery_app')),key,transport)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(run)
        try:
            assert entered.wait(5)
            assert pool.submit(run).result(timeout=5) is None
        finally:release.set()
        assert first.result(timeout=5)=='sent'
    assert calls==[1]


def test_exhausted_or_invalid_evidence_never_reaches_transport(ledger):
    store,_,_=ledger;transport=ExpenseAlertTransport('fixture-route',lambda *a:pytest.fail('no send'))
    with tenant_context(A):
        key=queued(store)
        with store._postgres.transaction() as c:c.execute("UPDATE expense_alert_outbox SET attempts=max_attempts WHERE alert_key=%s",(key,))
        assert deliver_alert(store,key,transport) is None
        key=queued(store)
        with store._postgres.transaction() as c:c.execute("UPDATE expense_alert_outbox SET payload='{}' WHERE alert_key=%s",(key,))
        assert deliver_alert(store,key,transport)=='operator_review'
