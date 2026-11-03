"""The parent's safe refusal survives channel EOF; raw vendor text does not."""
import httpx
import pytest
from requi_child_contract.broker import BrokerClient,BrokerUnavailable
from delivery_api.child_broker_channel import provider_broker_channel
from test_child_provider_broker import setup,RAW


@pytest.mark.parametrize('status,code',[(415,'invalid_request_error'),(401,'invalid_api_key'),(429,'rate_limit_exceeded')])
def test_safe_refusal_replaces_eof_but_never_leaks_vendor_body(status,code):
    def refuse(request):
        return httpx.Response(status,json={'error':{'code':code,'message':'PRIVATE_PROJECT Bearer secret-value'}})
    broker,g,j,calls=setup(refuse)
    with pytest.raises(RuntimeError,match=f'HTTP {status}') as error:
        with provider_broker_channel(broker) as fd:
            client=BrokerClient(fd.fileno(),next_call_id=lambda stage:'call-1')
            try:
                with pytest.raises(BrokerUnavailable):client.request(stage='intent',provider='deepseek',model='deepseek-flash',raw=RAW)
                raise RuntimeError('child descriptor closed before terminal event')
            finally:client.close()
    message=str(error.value)
    assert code in message and 'reconcile' in message.lower()
    assert 'PRIVATE_PROJECT' not in message and 'secret-value' not in message
    assert g.calls==['reserve','start','uncertain'] and len(calls)==1


def test_unknown_broker_error_is_not_promoted_as_safe_customer_text():
    broker,g,j,calls=setup()
    def fail(value):raise RuntimeError('PRIVATE_PROJECT secret-value')
    broker.dispatch=fail
    with pytest.raises(RuntimeError,match='child descriptor closed'):
        with provider_broker_channel(broker) as fd:
            client=BrokerClient(fd.fileno(),next_call_id=lambda stage:'call-1')
            try:
                with pytest.raises(BrokerUnavailable):client.request(stage='intent',provider='deepseek',model='deepseek-flash',raw=RAW)
                raise RuntimeError('child descriptor closed before terminal event')
            finally:client.close()
