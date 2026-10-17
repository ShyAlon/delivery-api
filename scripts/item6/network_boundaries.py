"""Explicitly constructed Mac-only network seams. No ambient clients or retries."""
import httpx,re,time
from delivery_api.paypal_adapter import PayPalBinding
from delivery_api.paypal_transport import PayPalHTTPTransport
from delivery_api.payment_errors import PaymentRejected,PaymentUncertain


class FixedOriginTransport(httpx.BaseTransport):
    def __init__(self,inner,origin,allowed):self.inner,self.origin,self.allowed=inner,origin,allowed
    def handle_request(self,request):
        u=request.url
        actual=f'{u.scheme}://{u.host}'+(':'+str(u.port) if u.port not in (None,443) else '')
        if actual!=self.origin or not self.allowed(request.method,u.path) or u.username or u.password:
            raise ValueError('fixed reviewed origin/path required')
        response=self.inner.handle_request(request)
        if 300<=response.status_code<400:response.close();raise ValueError('redirect forbidden')
        return response
    def close(self):self.inner.close()


class SandboxOAuth:
    def __init__(self,*,client,client_id,client_secret,binding,verified_app_merchant):
        if not isinstance(binding,PayPalBinding) or binding.live_mode is not False or verified_app_merchant!=binding.merchant_id:
            raise ValueError('verified sandbox app/merchant binding required')
        if not client_id or not client_secret:raise ValueError('local sandbox credentials required')
        self.client=client;self.credentials=(client_id,client_secret);self.token=None;self.expires=0
    def __call__(self):
        if self.token and time.monotonic()<self.expires:return self.token
        try:
            response=self.client.post('https://api-m.sandbox.paypal.com/v1/oauth2/token',
                auth=self.credentials,data={'grant_type':'client_credentials'},timeout=20,follow_redirects=False)
        except httpx.HTTPError:raise PaymentUncertain('sandbox OAuth unavailable; stop without capture') from None
        if response.status_code!=200 or len(response.content)>65536:raise PaymentRejected('sandbox OAuth rejected')
        try:
            value=response.json();token=value['access_token'];ttl=value['expires_in']
            if value.get('token_type','').lower()!='bearer' or not isinstance(token,str) or not token or len(token)>8192 or any(ord(c)<33 for c in token) or type(ttl) is not int or ttl<60:raise ValueError()
        except (ValueError,KeyError,TypeError):raise PaymentRejected('sandbox OAuth response malformed') from None
        self.token=token;self.expires=time.monotonic()+min(ttl-30,3600);return self.token


def sandbox_clients(*,inner,client_id,client_secret,binding,verified_app_merchant):
    def allowed(method,path):
        return (method=='POST' and path in ('/v1/oauth2/token','/v2/checkout/orders','/v1/notifications/verify-webhook-signature')) or bool(
            re.fullmatch(r'/v2/checkout/orders/[A-Za-z0-9_-]{1,127}(/capture)?',path) and method in ('GET','POST')) or bool(method=='GET' and re.fullmatch(r'/v2/payments/captures/[A-Za-z0-9_-]{1,127}',path))
    client=httpx.Client(transport=FixedOriginTransport(inner,'https://api-m.sandbox.paypal.com',allowed),trust_env=False,follow_redirects=False)
    oauth=SandboxOAuth(client=client,client_id=client_id,client_secret=client_secret,binding=binding,verified_app_merchant=verified_app_merchant)
    return client,PayPalHTTPTransport(binding=binding,client=client,token_supplier=oauth)


def deepseek_client_factory(*,inner_sync,inner_async,owner_module):
    # Parameter selects the interview or generator's own wire wrappers, not a
    # provider URL. Credentials remain supplied by the local SDK mechanism.
    from importlib import import_module
    if owner_module not in ('interview_engine','spec_generator'):raise ValueError('known engine module required')
    module=import_module(owner_module+'.provider_transport')
    def factory(*,stage,provider,model,gateway):
        if provider!='deepseek':raise ValueError('DeepSeek only for this acceptance run')
        allowed=lambda method,path:method=='POST' and path in ('/chat/completions','/v1/chat/completions')
        sync=FixedOriginTransport(inner_sync(),'https://api.deepseek.com',allowed)
        class AsyncFixed(httpx.AsyncBaseTransport):
            def __init__(self):self.inner=inner_async()
            async def handle_async_request(self,request):
                if str(request.url.copy_with(path='',query=None,fragment=None)) not in ('https://api.deepseek.com','https://api.deepseek.com/') or not allowed(request.method,request.url.path):raise ValueError('fixed DeepSeek SDK origin/path required')
                response=await self.inner.handle_async_request(request)
                if 300<=response.status_code<400:await response.aclose();raise ValueError('redirect forbidden')
                return response
            async def aclose(self):await self.inner.aclose()
        return (httpx.Client(transport=module.AdmittedTransport(sync,gateway,stage=stage,provider=provider,model=model),trust_env=False,follow_redirects=False),
            httpx.AsyncClient(transport=module.AsyncAdmittedTransport(AsyncFixed(),gateway,stage=stage,provider=provider,model=model),trust_env=False,follow_redirects=False))
    return factory


def deepseek_balance_reader(*,client,api_key):
    """Fixed public provider account endpoint; caller owns local key authority."""
    def read():
        import datetime
        response=client.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+api_key},timeout=20,follow_redirects=False)
        if response.status_code!=200 or len(response.content)>65536:raise ValueError('provider balance read unavailable')
        return {'response':response.json(),'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return read
