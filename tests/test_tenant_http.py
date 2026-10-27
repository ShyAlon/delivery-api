from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from delivery_api.tenant_http import TenantMiddleware
from delivery_api.tenancy import TenantScope,current_scope,TenantAccessDenied

A='00000000-0000-0000-0000-000000000001'
B='00000000-0000-0000-0000-000000000002'
class Verifier:
    def verify(self,token):
        if token not in ('a','b'):raise TenantAccessDenied('bad')
        return TenantScope(A if token=='a' else B)


def test_all_data_routes_require_identity_and_static_can_load():
    app=FastAPI()
    @app.get('/')
    def shell():return {'shell':True}
    @app.get('/api/data')
    def data():return {'owner':current_scope().owner_id}
    @app.get('/api/stream')
    async def stream():
        async def body(): yield current_scope().owner_id
        return StreamingResponse(body())
    app.add_middleware(TenantMiddleware,verifier=Verifier())
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/data').status_code==401
        assert client.get('/api/data',headers={'Authorization':'Bearer bad'}).status_code==401
        for token,owner in [('a',A),('b',B)]:
            assert client.get('/api/data',headers={'Authorization':'Bearer '+token}).json()['owner']==owner
            assert client.get('/api/stream',headers={'Authorization':'Bearer '+token}).text==owner
        assert client.get('/api/data').status_code==401
        assert client.post('/api/tasks/t/start',headers={'Authorization':'Bearer a'}).status_code==503
        assert client.post('/api/auth/signup').status_code==404


def test_copied_verified_token_rejected_after_approved_cache_window(monkeypatch):
    from delivery_api.tenant_http import SessionCheckCache
    active=[True]; now=[100.0]; checks=[]
    original_init=SessionCheckCache.__init__
    def init(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        self._time=lambda:now[0]
    monkeypatch.setattr(SessionCheckCache,'__init__',init)
    class SessionVerifier:
        def verify(self,token):return TenantScope(A,"00000000-0000-0000-0000-0000000000a1")
    app=FastAPI()
    @app.get('/api/data')
    def data():return {'owner':current_scope().owner_id}
    def check():checks.append(now[0]);return active[0]
    app.add_middleware(TenantMiddleware,verifier=SessionVerifier(),session_check=check)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer a'}
        assert client.get('/api/data',headers=headers).status_code==200
        active[0]=False;now[0]=129.99
        assert client.get('/api/data',headers=headers).status_code==200
        assert checks==[100.0]
        now[0]=130.0
        assert client.get('/api/data',headers=headers).status_code==401
        assert checks==[100.0,130.0]



def test_authorization_failure_does_not_confirm_resource_existence():
    app=FastAPI()
    @app.get('/api/private')
    def private():raise TenantAccessDenied('private owner detail')
    app.add_middleware(TenantMiddleware,verifier=Verifier())
    with TestClient(app) as client:
        response=client.get('/api/private',headers={'Authorization':'Bearer a'})
        assert response.status_code==404
        assert response.json()=={'detail':'Tenant resource not found'}


def test_unrecognized_non_api_routes_are_private_by_default():
    app=FastAPI()
    @app.get('/new-data-route')
    def new_data():return {'owner':current_scope().owner_id}
    app.add_middleware(TenantMiddleware,verifier=Verifier())
    with TestClient(app) as client:
        assert client.get('/new-data-route').status_code==401
        assert client.get('/new-data-route',headers={'Authorization':'Bearer a'}).json()=={'owner':A}
        assert client.get('/docs').status_code==401
