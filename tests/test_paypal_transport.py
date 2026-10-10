import pytest,httpx
from delivery_api.paypal_transport import PayPalHTTPTransport
from delivery_api.paypal_adapter import PaymentRejected,PaymentUncertain
from test_paypal_adapter import BINDING


def transport(handler):
    return PayPalHTTPTransport(binding=BINDING,client=httpx.Client(transport=httpx.MockTransport(handler)),token_supplier=lambda:'test-transient-token')


def test_fixed_sandbox_origin_and_server_token():
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(200,json={'id':'order-test'})
    t=transport(handler)
    assert t.request('POST','/v2/checkout/orders',{}, {'PayPal-Request-Id':'test-key'})=={'id':'order-test'}
    assert str(calls[0].url)=='https://api-m.sandbox.paypal.com/v2/checkout/orders'
    assert calls[0].headers['Authorization']=='Bearer test-transient-token'


@pytest.mark.parametrize('status',[301,302,400,401,403,404,422])
def test_rejected_or_redirected_response_does_not_follow(status):
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(status,headers={'Location':'https://attacker.invalid/'})
    with pytest.raises(PaymentRejected):transport(handler).request('GET','/v2/checkout/orders/order-test')
    assert len(calls)==1


@pytest.mark.parametrize('status',[408,429,500,503])
def test_remote_error_does_not_retry_or_change_request_id(status):
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(status)
    with pytest.raises(PaymentUncertain):transport(handler).request('POST','/v2/checkout/orders/order-test/capture',{})
    assert len(calls)==1


@pytest.mark.parametrize('path',['https://attacker.invalid/','/v2/checkout/orders/../../bad','/v2/checkout/orders/order?token=1','/v2/payments/captures/x/refund'])
def test_resource_urls_and_money_refund_routes_not_supported(path):
    t=transport(lambda _:pytest.fail('must not call network'))
    with pytest.raises(PaymentRejected):t.request('GET',path)


def test_caller_cannot_override_bearer_identity():
    with pytest.raises(PaymentRejected):transport(lambda _:pytest.fail('not called')).request('GET','/v2/checkout/orders/x',headers={'authorization':'other'})


def test_timeout_and_invalid_success_body_require_reconciliation():
    def timeout(request):raise httpx.ReadTimeout('test')
    with pytest.raises(PaymentUncertain):transport(timeout).request('GET','/v2/checkout/orders/x')
    with pytest.raises(PaymentUncertain):transport(lambda _:httpx.Response(200,content=b'not-json')).request('GET','/v2/checkout/orders/x')
