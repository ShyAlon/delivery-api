from copy import deepcopy
from dataclasses import replace
import json,pytest
from delivery_api.paypal_adapter import PayPalAdapter,PayPalBinding,PaymentRejected,PaymentUncertain
from delivery_api.payment_offers import purchase_intent

BINDING=PayPalBinding('merchant-test','webhook-test',False)
INTENT=purchase_intent(purchase_id='purchase-test',owner_id='00000000-0000-0000-0000-000000000001',offer_id='credits-12',units_per_credit=1000)
HEADERS={'PAYPAL-AUTH-ALGO':'SHA256withRSA','PAYPAL-CERT-URL':'https://api.paypal.com/cert',
 'PAYPAL-TRANSMISSION-ID':'transmission','PAYPAL-TRANSMISSION-SIG':'signed-value',
 'PAYPAL-TRANSMISSION-TIME':'2026-10-10T05:00:00Z'}


def order(intent=INTENT,status='COMPLETED'):
    return {'id':'order-test','intent':'CAPTURE','status':status,'purchase_units':[{
        'reference_id':intent.purchase_id,'invoice_id':intent.purchase_id,'custom_id':intent.digest,
        'payee':{'merchant_id':BINDING.merchant_id},'amount':{'currency_code':'USD','value':'11.50'},
        'payments':{'captures':[{'id':'capture-test','status':'COMPLETED','final_capture':True,
        'amount':{'currency_code':'USD','value':'11.50'}}]}}]}


def capture():
    return {'id':'capture-test','status':'COMPLETED','final_capture':True,
        'amount':{'currency_code':'USD','value':'11.50'},'supplementary_data':{'related_ids':{'order_id':'order-test'}}}


class Stub:
    binding=BINDING
    def __init__(self):self.order=order();self.capture=capture();self.verify='SUCCESS';self.calls=[];self.error=None
    def request(self,method,path,payload=None,headers=None):
        self.calls.append((method,path,deepcopy(payload),headers))
        if self.error:raise self.error
        if path=='/v1/notifications/verify-webhook-signature':return {'verification_status':self.verify}
        if path=='/v2/payments/captures/capture-test':return deepcopy(self.capture)
        return deepcopy(self.order)


@pytest.fixture
def adapter():return PayPalAdapter(Stub(),BINDING)


def test_create_exact_server_price_merchant_and_distinct_stable_keys(adapter):
    adapter.create(INTENT)
    method,path,payload,headers=adapter.transport.calls[0]
    assert payload['purchase_units'][0]['amount']=={'currency_code':'USD','value':'11.50'}
    assert payload['purchase_units'][0]['custom_id']==INTENT.digest
    assert headers['PayPal-Request-Id']==adapter.request_id(INTENT,'create')
    assert adapter.request_id(INTENT,'create')!=adapter.request_id(INTENT,'capture')
    assert adapter.request_id(INTENT,'create')!=adapter.request_id(replace(INTENT,owner_id='00000000-0000-0000-0000-000000000002'),'create')


def test_browser_approved_order_is_not_paid(adapter):
    adapter.transport.order['status']='APPROVED'
    assert adapter.completed_evidence(INTENT,'order-test',event_id='event-1') is None


@pytest.mark.parametrize('status',['PENDING','DECLINED','REFUNDED','PARTIALLY_REFUNDED','FAILED'])
def test_noncompleted_capture_never_grants_credits(adapter,status):
    adapter.transport.capture['status']=status
    assert adapter.completed_evidence(INTENT,'order-test',event_id='event-1') is None


@pytest.mark.parametrize('field,value',[
 ('custom_id','other'),('reference_id','foreign'),('invoice_id','foreign'),
 ('payee',{'merchant_id':'wrong'}),('amount',{'currency_code':'USD','value':'11.51'}),
 ('amount',{'currency_code':'EUR','value':'11.50'}),('amount',{'currency_code':'USD','value':11.5})])
def test_wrong_purchase_merchant_or_money_rejected(adapter,field,value):
    adapter.transport.order['purchase_units'][0][field]=value
    with pytest.raises(PaymentRejected):adapter.completed_evidence(INTENT,'order-test',event_id='event-1')


@pytest.mark.parametrize('change',[
 {'id':'other-capture'},{'final_capture':False},{'supplementary_data':{'related_ids':{'order_id':'foreign'}}},
 {'amount':{'currency_code':'USD','value':'11.49'}},{'payee':{'merchant_id':'foreign'}}])
def test_authoritative_capture_checks_fail_closed(adapter,change):
    adapter.transport.capture.update(change)
    with pytest.raises(PaymentRejected):adapter.completed_evidence(INTENT,'order-test',event_id='event-1')


def test_valid_capture_is_bound_to_immutable_purchase(adapter):
    evidence=adapter.completed_evidence(INTENT,'order-test',event_id='event-1',capture_id='capture-test')
    assert evidence.status=='paid' and evidence.amount_minor==1150 and evidence.intent_digest==INTENT.digest


def test_capture_post_only_after_approved_and_ambiguous_timeout_propagates(adapter):
    adapter.transport.order['status']='CREATED'
    assert adapter.capture(INTENT,'order-test') is None
    assert len(adapter.transport.calls)==1
    adapter.transport.error=PaymentUncertain('timeout')
    with pytest.raises(PaymentUncertain):adapter.capture(INTENT,'order-test')


def event():return {'id':'event-1','event_type':'PAYMENT.CAPTURE.COMPLETED','resource':capture()}


def test_webhook_verifies_exact_body_and_configured_webhook_id_no_resource_urls(adapter):
    body=json.dumps(event()).encode()
    verified,digest=adapter.verify_event(body,HEADERS)
    assert verified==event() and len(digest)==64
    call=adapter.transport.calls[0]
    assert call[1]=='/v1/notifications/verify-webhook-signature'
    assert call[2]['webhook_id']==BINDING.webhook_id and call[2]['webhook_event']==event()


@pytest.mark.parametrize('result',['FAILURE','success',None])
def test_invalid_signature_never_returns_event(adapter,result):
    adapter.transport.verify=result
    with pytest.raises(PaymentRejected):adapter.verify_event(json.dumps(event()).encode(),HEADERS)


@pytest.mark.parametrize('body',[b'{"id":"one","id":"two"}',b'[]',b'NaN',b'invalid',b'x'*262145])
def test_ambiguous_or_malformed_webhook_fails_before_verify(adapter,body):
    with pytest.raises(PaymentRejected):adapter.verify_event(body,HEADERS)
    assert not adapter.transport.calls


def test_missing_signed_headers_and_unbound_transport_rejected(adapter):
    with pytest.raises(PaymentRejected):adapter.verify_event(json.dumps(event()).encode(),{})
    with pytest.raises(PaymentRejected):PayPalAdapter(Stub(),replace(BINDING,live_mode=True))


def test_reviewed_q01_packs_and_overflow_are_exact():
    from delivery_api.payment_offers import OFFERS
    assert OFFERS=={'credits-5':(5,500),'credits-12':(12,1150),'credits-25':(25,2300),'credits-50':(50,4500)}
    with pytest.raises(ValueError):purchase_intent(purchase_id='x',owner_id=INTENT.owner_id,offer_id='credits-50',units_per_credit=2**62)


def test_actual_processor_fee_net_not_estimate(adapter):
    adapter.transport.capture['seller_receivable_breakdown']={
        'gross_amount':{'currency_code':'USD','value':'11.50'},
        'paypal_fee':{'currency_code':'USD','value':'0.95'},
        'net_amount':{'currency_code':'USD','value':'10.55'}}
    evidence=adapter.completed_evidence(INTENT,'order-test',event_id='event-1')
    assert evidence.processor_fee_minor==95 and evidence.net_received_minor==1055
    adapter.transport.capture['seller_receivable_breakdown']['net_amount']['value']='10.56'
    with pytest.raises(PaymentRejected):adapter.completed_evidence(INTENT,'order-test',event_id='event-1')


def test_official_refund_up_link_extracts_id_without_following_supplied_url(adapter):
    event={'id':'event-refund','event_type':'PAYMENT.CAPTURE.REFUNDED','resource':{
        'id':'refund-1','links':[{'rel':'up','method':'GET','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/capture-test'}]}}
    notice=adapter.payment_notice(event)
    assert notice['payment_id']=='capture-test' and notice['action']=='review'
    assert adapter.transport.calls[0][1]=='/v2/payments/captures/capture-test'
    event['resource']['links'][0]['href']='https://attacker.invalid/v2/payments/captures/capture-test'
    with pytest.raises(PaymentRejected):adapter.payment_notice(event)
