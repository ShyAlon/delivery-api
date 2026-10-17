"""Fixed-origin DeepSeek transports and the account balance read. No ambient clients, no redirects, no retries."""
import httpx


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
