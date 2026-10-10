import importlib.util
from pathlib import Path
import httpx,pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('item6_network',ROOT/'scripts/item6/network_boundaries.py');n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n)
from test_paypal_adapter import BINDING


def test_sandbox_oauth_credential_origin_cache_no_redirect_no_live_access():
 calls=[]
 def handler(request):
  calls.append(request)
  return httpx.Response(200,json={'access_token':'fake-transient','expires_in':300,'token_type':'Bearer'})
 c,t=n.sandbox_clients(inner=httpx.MockTransport(handler),client_id='fake-client',client_secret='fake-secret',binding=BINDING,verified_app_merchant=BINDING.merchant_id)
 assert t.token_supplier()=='fake-transient' and t.token_supplier()=='fake-transient' and len(calls)==1
 assert str(calls[0].url)=='https://api-m.sandbox.paypal.com/v1/oauth2/token'
 with pytest.raises(ValueError):c.post('https://api-m.paypal.com/v1/oauth2/token')
 with pytest.raises(ValueError):c.post('https://api-m.sandbox.paypal.com/other')
 assert len(calls)==1


@pytest.mark.parametrize('status',[301,401,429,500])
def test_oauth_error_never_contains_local_secret_and_does_not_retry(status):
 calls=[]
 def handler(request):calls.append(request);return httpx.Response(status,text='fake-secret')
 c,t=n.sandbox_clients(inner=httpx.MockTransport(handler),client_id='client',client_secret='fake-secret',binding=BINDING,verified_app_merchant=BINDING.merchant_id)
 with pytest.raises((ValueError,n.PaymentRejected,n.PaymentUncertain)) as error:t.token_supplier()
 assert 'fake-secret' not in str(error.value) and len(calls)==1


def test_live_binding_or_unverified_app_account_denied_before_network():
 from dataclasses import replace
 for binding,merchant in [(replace(BINDING,live_mode=True),BINDING.merchant_id),(BINDING,'foreign')]:
  with pytest.raises(ValueError):n.sandbox_clients(inner=httpx.MockTransport(lambda r:pytest.fail('no network')),client_id='x',client_secret='y',binding=binding,verified_app_merchant=merchant)


def test_deepseek_factory_no_other_provider_and_fixed_origin_before_gateway():
 pytest.importorskip('spec_generator.provider_transport')
 factory=n.deepseek_client_factory(inner_sync=lambda:httpx.MockTransport(lambda r:pytest.fail('no network')),inner_async=lambda:httpx.MockTransport(lambda r:pytest.fail('no network')),owner_module='spec_generator')
 with pytest.raises(ValueError):factory(stage='needs',provider='openai',model='x',gateway=None)
