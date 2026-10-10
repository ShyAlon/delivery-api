"""Requires disposable PostgreSQL with private migration and restricted login."""
import os,uuid,json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from delivery_api.payment_repository import PaymentRepository
from delivery_api.payment_service import PaymentService
from delivery_api.payment_contract import prepare_fulfillment
from delivery_api.paypal_adapter import PayPalAdapter,PaymentRejected
from test_paypal_adapter import BINDING,INTENT,Stub,HEADERS,order,capture

DSN=os.getenv('PAYMENT_TEST_POSTGRES_DSN')
ADMIN=os.getenv('PAYMENT_TEST_ADMIN_DSN')
pytestmark=pytest.mark.skipif(not DSN or not ADMIN,reason='isolated private payment PostgreSQL required')

@pytest.fixture
def repo():return PaymentRepository(DSN,BINDING.ledger_binding)

@pytest.fixture
def purchase(repo):
    import psycopg
    intent=replace(INTENT,purchase_id='payment-test-'+uuid.uuid4().hex)
    with psycopg.connect(ADMIN) as c:
        c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000,0) ON CONFLICT(owner_id) DO UPDATE SET balance_units=0,units_per_credit=1000',(intent.owner_id,))
    repo.create_purchase(intent)
    adapter=PayPalAdapter(Stub(),BINDING)
    repo.bind_request_ids(intent.purchase_id,adapter.request_id(intent,'create'),adapter.request_id(intent,'capture'))
    repo.bind_order(intent.purchase_id,'order-'+uuid.uuid4().hex)
    return intent


def evidence(repo,intent,cid=None):
    from delivery_api.payment_contract import ProcessorEvidence
    return ProcessorEvidence('paypal',BINDING.merchant_id,'event-'+uuid.uuid4().hex,cid or 'capture-'+uuid.uuid4().hex,intent.purchase_id,intent.digest,'USD',1150,'paid',False)


def fulfill(repo,intent,e):
    plan=prepare_fulfillment(intent,e,expected_processor='paypal',expected_merchant=BINDING.merchant_id,expected_live_mode=False)
    return repo.fulfill(intent,e,plan)


def balance():
    import psycopg
    with psycopg.connect(ADMIN) as c:return c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(INTENT.owner_id,)).fetchone()[0]


def receipt(kind='PAYMENT.CAPTURE.REVERSED'):
    return {'event_id':'event-'+uuid.uuid4().hex,'digest':'a'*64,'kind':kind}


def test_payment_credit_and_many_parallel_callback_paths_post_once(repo,purchase):
    e=evidence(repo,purchase)
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda _:fulfill(repo,purchase,e),range(24)))
    assert sum(r['credit_posted'] for r in results)==1
    assert balance()==12000


def test_distinct_payment_cannot_recredit_purchase(repo,purchase):
    fulfill(repo,purchase,evidence(repo,purchase))
    with pytest.raises(Exception):fulfill(repo,purchase,evidence(repo,purchase))
    assert balance()==12000


def test_payment_identity_cannot_fulfill_two_owners_or_purchases(repo,purchase):
    cid='capture-'+uuid.uuid4().hex
    fulfill(repo,purchase,evidence(repo,purchase,cid))
    other=replace(purchase,purchase_id='other-'+uuid.uuid4().hex,owner_id='00000000-0000-0000-0000-000000000002')
    repo.create_purchase(other)
    adapter=PayPalAdapter(Stub(),BINDING)
    repo.bind_request_ids(other.purchase_id,adapter.request_id(other,'create'),adapter.request_id(other,'capture'))
    repo.bind_order(other.purchase_id,'order-'+uuid.uuid4().hex)
    with pytest.raises(Exception):fulfill(repo,other,evidence(repo,other,cid))
    assert balance()==12000


def test_adverse_first_blocks_late_capture_no_automatic_debit(repo,purchase):
    row=repo.get_purchase(purchase.purchase_id)
    repo.record_review(row,receipt())
    assert fulfill(repo,purchase,evidence(repo,purchase))=={'state':'operator_review','credit_posted':False}
    assert balance()==0


def test_adverse_after_paid_preserves_balance_and_is_idempotent(repo,purchase):
    e=evidence(repo,purchase);fulfill(repo,purchase,e)
    r=receipt();row=repo.get_purchase(purchase.purchase_id)
    first=repo.record_review(row,r)
    assert repo.record_review(row,r)==first
    assert fulfill(repo,purchase,e)['credit_posted'] is False
    assert balance()==12000
    with pytest.raises(PaymentRejected):repo.event_result(dict(r,digest='b'*64))


def test_event_receipt_and_credit_effect_rollback_together(repo,purchase,monkeypatch):
    e=evidence(repo,purchase)
    plan=prepare_fulfillment(purchase,e,expected_processor='paypal',expected_merchant=BINDING.merchant_id,expected_live_mode=False)
    r=receipt('PAYMENT.CAPTURE.COMPLETED')
    original=repo._receipt
    def fail(*args,**kwargs):raise RuntimeError('injected receipt failure')
    monkeypatch.setattr(repo,'_receipt',fail)
    with pytest.raises(RuntimeError):repo.fulfill(purchase,e,plan,receipt=r)
    assert balance()==0 and repo.event_result(r) is None
    monkeypatch.setattr(repo,'_receipt',original)
    assert repo.fulfill(purchase,e,plan,receipt=r)['credit_posted']
    assert balance()==12000


def test_scale_mismatch_and_overflow_rollback(repo,purchase):
    import psycopg
    with psycopg.connect(ADMIN) as c:c.execute('UPDATE credit_accounts SET units_per_credit=2000 WHERE owner_id=%s',(purchase.owner_id,))
    with pytest.raises(Exception):fulfill(repo,purchase,evidence(repo,purchase))
    assert balance()==0
    with psycopg.connect(ADMIN) as c:c.execute('UPDATE credit_accounts SET units_per_credit=1000,balance_units=9223372036854775800 WHERE owner_id=%s',(purchase.owner_id,))
    with pytest.raises(Exception):fulfill(repo,purchase,evidence(repo,purchase))
    assert balance()==9223372036854775800
    assert not repo.get_purchase(purchase.purchase_id)['credit_posted']


def test_no_cross_namespace_mutation_and_immutable_binding(repo,purchase):
    with pytest.raises(PaymentRejected):PaymentRepository(DSN,replace(BINDING,merchant_id='other').ledger_binding).get_purchase(purchase.purchase_id)
    with pytest.raises(PaymentRejected):repo.bind_order(purchase.purchase_id,'different-order')
    with repo.transaction() as c:
        with pytest.raises(Exception):c.execute('UPDATE payment_purchases SET amount_minor=1 WHERE purchase_id=%s',(purchase.purchase_id,))


def test_browser_and_tenant_roles_cannot_access_ingest_tables_or_function(repo,purchase):
    import psycopg
    tenant=DSN.replace('user=delivery_payment_test','user=delivery_app')
    with pytest.raises(PaymentRejected):PaymentRepository(tenant,BINDING.ledger_binding)
    with psycopg.connect(tenant) as c:
        c.execute('SET LOCAL ROLE requi_backend')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM payment_purchases')
    with repo.transaction() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('UPDATE credit_accounts SET balance_units=999')


def test_purchase_replay_different_digest_is_rejected(repo,purchase):
    assert repo.create_purchase(purchase)['intent']==purchase
    with pytest.raises(PaymentRejected):repo.create_purchase(replace(purchase,credit_units=1))


def service(repo,intent):
    row=repo.get_purchase(intent.purchase_id)
    remote=Stub();remote.order=order(intent)
    remote.order['id']=row['order_id'];remote.capture=capture()
    cid='capture-'+uuid.uuid4().hex
    remote.order['purchase_units'][0]['payments']['captures'][0]['id']=cid
    remote.capture['id']=cid
    base_request=remote.request
    def request(method,path,payload=None,headers=None):
        if path=='/v2/payments/captures/'+cid:
            remote.calls.append((method,path,payload,headers))
            return dict(remote.capture)
        return base_request(method,path,payload,headers)
    remote.request=request
    remote.capture['supplementary_data']['related_ids']['order_id']=row['order_id']
    return PaymentService(PayPalAdapter(remote,BINDING),repo)


def webhook(s,kind,resource):
    body=json.dumps({'id':'event-'+uuid.uuid4().hex,'event_type':kind,'resource':resource}).encode()
    return s.webhook(body,HEADERS),body


def test_order_capture_webhook_concurrency_end_to_end(repo,purchase):
    s=service(repo,purchase)
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda i:s.capture(purchase.purchase_id) if i%2 else webhook(s,'PAYMENT.CAPTURE.COMPLETED',s.adapter.transport.capture)[0],range(20)))
    assert sum(r['credit_posted'] for r in results)==1 and balance()==12000


def test_refund_before_capture_binding_resolves_remote_order_and_blocks_paid(repo,purchase):
    s=service(repo,purchase)
    resource={'id':'refund-1','supplementary_data':{'related_ids':{'capture_id':s.adapter.transport.capture['id']}}}
    result,body=webhook(s,'PAYMENT.CAPTURE.REFUNDED',resource)
    assert result['state']=='operator_review'
    assert s.capture(purchase.purchase_id)['credit_posted'] is False
    assert balance()==0 and s.webhook(body,HEADERS)==result


def test_dispute_before_capture_binding_looks_up_seller_transaction(repo,purchase):
    s=service(repo,purchase)
    result,_=webhook(s,'CUSTOMER.DISPUTE.CREATED',{'id':'dispute-1','disputed_transactions':[{'seller_transaction_id':s.adapter.transport.capture['id']}]})
    assert result['state']=='operator_review' and balance()==0
    assert not s.capture(purchase.purchase_id)['credit_posted']


def test_verified_pending_then_completed_gives_one_credit_only(repo,purchase):
    s=service(repo,purchase)
    result,_=webhook(s,'PAYMENT.CAPTURE.PENDING',s.adapter.transport.capture)
    assert result['state']=='awaiting_payment' and balance()==0
    assert webhook(s,'PAYMENT.CAPTURE.COMPLETED',s.adapter.transport.capture)[0]['credit_posted']
    assert balance()==12000


def test_wrong_signature_never_journals_or_posts(repo,purchase):
    s=service(repo,purchase);s.adapter.transport.verify='FAILURE'
    with pytest.raises(PaymentRejected):webhook(s,'PAYMENT.CAPTURE.COMPLETED',s.adapter.transport.capture)
    assert balance()==0


def test_wrong_capture_event_order_is_rejected(repo,purchase):
    s=service(repo,purchase)
    resource=dict(s.adapter.transport.capture,supplementary_data={'related_ids':{'order_id':'wrong'}})
    with pytest.raises(PaymentRejected):webhook(s,'PAYMENT.CAPTURE.COMPLETED',resource)
    assert balance()==0


def test_review_vs_paid_race_never_debits_and_never_reopens(repo,purchase):
    s=service(repo,purchase)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(s.capture,purchase.purchase_id),pool.submit(webhook,s,'PAYMENT.CAPTURE.REVERSED',s.adapter.transport.capture)]
        for f in futures:f.result()
    assert repo.get_purchase(purchase.purchase_id)['state']=='operator_review'
    before=balance();assert before in (0,12000)
    assert s.capture(purchase.purchase_id)['credit_posted'] is False
    assert balance()==before


def test_expired_ambiguous_create_key_is_not_reused_to_charge_again(repo,purchase):
    import psycopg,time
    from delivery_api.paypal_adapter import PaymentUncertain
    with psycopg.connect(ADMIN) as c:
        c.execute('UPDATE payment_purchases SET create_started_at=%s WHERE purchase_id=%s',(time.time()-6*3600,purchase.purchase_id))
    with pytest.raises(PaymentUncertain):repo.begin_remote_operation(purchase.purchase_id,'create',replay_window_seconds=5*3600)
    assert balance()==0


def test_duplicate_create_recovers_same_order_without_new_remote_charge(repo,purchase):
    s=service(repo,purchase)
    assert s.create(purchase.purchase_id)['order_id']==repo.get_purchase(purchase.purchase_id)['order_id']
    assert all(call[0]=='GET' for call in s.adapter.transport.calls)


def test_many_same_event_races_receipt_and_payment_credit_are_atomic(repo,purchase):
    s=service(repo,purchase)
    body=json.dumps({'id':'event-'+uuid.uuid4().hex,'event_type':'PAYMENT.CAPTURE.COMPLETED','resource':s.adapter.transport.capture}).encode()
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda _:s.webhook(body,HEADERS),range(16)))
    assert balance()==12000 and all(r['state']=='fulfilled' for r in results)
    # Replayed event returns original outcome, not a second credit effect.
    assert repo.get_purchase(purchase.purchase_id)['credit_posted']


def test_verified_unknown_event_is_durable_review_not_owner_guess(repo,purchase):
    s=service(repo,purchase)
    s.adapter.transport.capture['supplementary_data']['related_ids']['order_id']='unknown-order'
    resource=dict(s.adapter.transport.capture)
    result,body=webhook(s,'PAYMENT.CAPTURE.REVERSED',resource)
    assert result['state']=='unmatched_operator_review' and balance()==0
    assert s.webhook(body,HEADERS)==result


def test_payment_role_cannot_set_credit_posted_directly(repo,purchase):
    import psycopg
    with repo.transaction() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('UPDATE payment_purchases SET credit_posted=true WHERE purchase_id=%s',(purchase.purchase_id,))


def test_receiving_report_keeps_pack_owner_actual_fee_and_unknowns_distinct(repo,purchase):
    import time
    e=replace(evidence(repo,purchase),processor_fee_minor=95,net_received_minor=1055)
    fulfill(repo,purchase,e)
    report=repo.receiving_cost_report(start_at=time.time()-60,end_at=time.time()+60,owner_id=purchase.owner_id)
    group=next(g for g in report['groups'] if g['offer_id']==purchase.offer_id)
    assert group['known_fee_minor']>=95 and group['known_net_minor']>=1055
    assert not report['profit_available'] and 'withdrawal/FX' in report['missing_cost_categories']
    assert group['owner_id']==purchase.owner_id


def test_conflicting_receiving_fee_replay_cannot_rewrite_ledger(repo,purchase):
    e=replace(evidence(repo,purchase),processor_fee_minor=95,net_received_minor=1055)
    fulfill(repo,purchase,e)
    with pytest.raises(Exception):fulfill(repo,purchase,replace(e,processor_fee_minor=90,net_received_minor=1060))
    assert balance()==12000


def test_create_persists_request_before_remote_call_and_reconciles_timeout(repo,purchase):
    from delivery_api.paypal_adapter import PaymentUncertain
    intent=replace(purchase,purchase_id='create-'+uuid.uuid4().hex)
    repo.create_purchase(intent)
    s=service(repo,purchase)
    s.adapter.transport.order=order(intent,status='CREATED')
    remote_order='new-order-'+uuid.uuid4().hex
    s.adapter.transport.order['id']=remote_order
    s.adapter.transport.order['links']=[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token='+remote_order}]
    original=s.adapter.transport.request;first=True
    def request(method,path,payload=None,headers=None):
        nonlocal first
        if path=='/v2/checkout/orders' and first:
            first=False
            assert repo.get_purchase(intent.purchase_id)['create_started_at'] is not None
            raise PaymentUncertain('remote created but reply lost')
        return original(method,path,payload,headers)
    s.adapter.transport.request=request
    with pytest.raises(PaymentUncertain):s.create(intent.purchase_id)
    assert repo.get_purchase(intent.purchase_id)['order_id'] is None
    assert s.create(intent.purchase_id)['order_id']==remote_order
    assert balance()==0


def test_adverse_before_local_order_binding_blocks_later_paid_reconciliation(repo,purchase):
    s=service(repo,purchase)
    remote_order='future-'+uuid.uuid4().hex
    s.adapter.transport.capture['supplementary_data']['related_ids']['order_id']=remote_order
    result,_=webhook(s,'PAYMENT.CAPTURE.REVERSED',s.adapter.transport.capture)
    assert result['state']=='unmatched_operator_review'
    later=replace(purchase,purchase_id='future-purchase-'+uuid.uuid4().hex)
    repo.create_purchase(later)
    repo.bind_request_ids(later.purchase_id,s.adapter.request_id(later,'create'),s.adapter.request_id(later,'capture'))
    repo.bind_order(later.purchase_id,remote_order)
    assert repo.get_purchase(later.purchase_id)['state']=='operator_review'
    assert not fulfill(repo,later,evidence(repo,later))['credit_posted'] and balance()==0


def test_namespace_mismatch_stops_service_before_provider_work(repo,purchase):
    wrong=PayPalAdapter(type('BoundStub',(),{'binding':replace(BINDING,live_mode=True)})(),replace(BINDING,live_mode=True))
    with pytest.raises(PaymentRejected):PaymentService(wrong,repo)


def test_adverse_binding_vs_fulfillment_uses_shared_order_lock(repo,purchase):
    s=service(repo,purchase)
    remote_order='race-order-'+uuid.uuid4().hex
    later=replace(purchase,purchase_id='race-purchase-'+uuid.uuid4().hex)
    repo.create_purchase(later)
    repo.bind_request_ids(later.purchase_id,s.adapter.request_id(later,'create'),s.adapter.request_id(later,'capture'))
    row=s.adapter.transport.capture;row['supplementary_data']['related_ids']['order_id']=remote_order
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(repo.bind_order,later.purchase_id,remote_order),pool.submit(webhook,s,'PAYMENT.CAPTURE.REVERSED',row)]
        for f in futures:f.result()
    assert repo.get_purchase(later.purchase_id)['state']=='operator_review'
    assert not fulfill(repo,later,evidence(repo,later))['credit_posted'] and balance()==0
