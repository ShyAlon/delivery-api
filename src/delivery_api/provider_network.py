"""Fixed-origin DeepSeek transports and the account balance read. No ambient clients, no redirects, no retries."""
import httpx


class FixedOriginTransport(httpx.BaseTransport):
    def __init__(self,inner,origin,allowed):self.inner,self.origin,self.allowed=inner,origin,allowed
    def validate(self,request):
        u=request.url
        actual=f'{u.scheme}://{u.host}'+(':'+str(u.port) if u.port not in (None,443) else '')
        if actual!=self.origin or not self.allowed(request.method,u.path) or u.username or u.password or u.query or u.fragment:
            raise ValueError('fixed reviewed origin/path required')
    def handle_request(self,request):
        self.validate(request)
        response=self.inner.handle_request(request)
        if 300<=response.status_code<400:response.close();raise ValueError('redirect forbidden')
        return response
    def close(self):self.inner.close()


def _clients(inner_sync,inner_async,owner_module,provider_name,origin,paths):
    from importlib import import_module
    if owner_module not in ('interview_engine','spec_generator'):raise ValueError('known engine module required')
    module=import_module(owner_module+'.provider_transport')
    def factory(*,stage,provider,model,gateway):
        if provider!=provider_name:raise ValueError(provider_name+' only for this acceptance run')
        allowed=lambda method,path:method=='POST' and path in paths
        sync=FixedOriginTransport(inner_sync(),origin,allowed)
        class AsyncFixed(httpx.AsyncBaseTransport):
            def __init__(self):self.inner=inner_async()
            async def handle_async_request(self,request):
                sync.validate(request)
                response=await self.inner.handle_async_request(request)
                if 300<=response.status_code<400:await response.aclose();raise ValueError('redirect forbidden')
                return response
            async def aclose(self):await self.inner.aclose()
        return (httpx.Client(transport=module.AdmittedTransport(sync,gateway,stage=stage,provider=provider,model=model),trust_env=False,follow_redirects=False),
            httpx.AsyncClient(transport=module.AsyncAdmittedTransport(AsyncFixed(),gateway,stage=stage,provider=provider,model=model),trust_env=False,follow_redirects=False))
    return factory


def deepseek_client_factory(*,inner_sync,inner_async,owner_module):
    return _clients(inner_sync,inner_async,owner_module,'deepseek','https://api.deepseek.com',('/chat/completions','/v1/chat/completions'))


def deepseek_balance_reader(*,client,api_key):
    """Fixed public provider account endpoint; caller owns local key authority."""
    def read():
        import datetime
        response=client.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+api_key},timeout=20,follow_redirects=False)
        if response.status_code!=200 or len(response.content)>65536:raise ValueError('provider balance read unavailable')
        return {'response':response.json(),'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return read



def ollama_client_factory(*,inner_sync,inner_async,owner_module):
    """Local Ollama SDK clients under the same admitted wire as DeepSeek.

    Origin and path come from OLLAMA_BASE_URL (the engine reads the same
    setting), never from the request. Credentials remain supplied by the local
    SDK mechanism; the fixed origin forbids redirects and ambient clients.
    """
    import os,urllib.parse
    base=(os.environ.get('OLLAMA_BASE_URL') or 'http://localhost:11434/v1').strip().rstrip('/')
    parsed=urllib.parse.urlsplit(base)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('plain reviewed Ollama base URL required')
    origin=f'{parsed.scheme}://{parsed.hostname}'+(':'+str(parsed.port) if parsed.port not in (None,443) else '')
    return _clients(inner_sync,inner_async,owner_module,'ollama',origin,(parsed.path+'/chat/completions',))
