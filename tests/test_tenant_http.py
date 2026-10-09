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


def test_copied_verified_token_rejected_after_session_revoked():
    active=[True]
    app=FastAPI()
    @app.get('/api/data')
    def data():return {'owner':current_scope().owner_id}
    app.add_middleware(TenantMiddleware,verifier=Verifier(),session_check=lambda:active[0])
    with TestClient(app) as client:
        assert client.get('/api/data',headers={'Authorization':'Bearer a'}).status_code==200
        active[0]=False
        assert client.get('/api/data',headers={'Authorization':'Bearer a'}).status_code==401

