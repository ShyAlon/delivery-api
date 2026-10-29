"""A signed-in page load must not authenticate every static file, and an unexpected error must reach the browser with its reference."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from delivery_api.tenant_http import TenantMiddleware
from delivery_api.tenancy import TenantScope


def test_static_files_and_health_are_never_authenticated_but_data_routes_still_are():
    owner='00000000-0000-0000-0000-000000000001';calls={'verify':0,'session':0}
    class Verifier:
        def verify(self,token):
            calls['verify']+=1;return TenantScope(owner)
    def session_check():
        calls['session']+=1;return True
    app=FastAPI()
    for path in ('/assets/app.js','/favicon.ico','/health'):
        app.get(path)(lambda:{'ok':True})
    app.get('/api/data')(lambda:{'ok':True})
    app.add_middleware(TenantMiddleware,verifier=Verifier(),session_check=session_check)
    headers={'Authorization':'Bearer a'}
    with TestClient(app) as client:
        for path in ('/assets/app.js','/favicon.ico','/health'):
            assert client.get(path,headers=headers).status_code==200
        assert calls=={'verify':0,'session':0}            # thirty files per page load cost no verification and no database query
        assert client.get('/api/data',headers=headers).status_code==200
        assert calls=={'verify':1,'session':1}


@pytest.fixture
def boom_route(pg_workspace_factory,pg_task_app):
    import delivery_api.app as module
    def boom():raise RuntimeError('secret postgresql://user:pass@host/db')
    module.app.router.add_api_route('/api/_boom',boom,methods=['GET'])
    try:
        with pg_workspace_factory() as workspace:
            yield pg_task_app(workspace),{'Authorization':'Bearer '+workspace.token}
    finally:
        module.app.router.routes.pop()


def test_an_unexpected_error_answers_with_its_reference_and_type_but_not_its_message(boom_route):
    app,headers=boom_route
    reply=TestClient(app,raise_server_exceptions=False).get('/api/_boom',headers=headers)
    assert reply.status_code==500
    detail=reply.json()['detail']
    ref=reply.headers['x-request-id']
    assert f'Reference {ref}, RuntimeError.'==detail['cause'] and 'secret' not in reply.text and 'postgresql' not in reply.text


def test_the_unexpected_error_still_reaches_the_server_and_tests(boom_route):
    app,headers=boom_route
    with pytest.raises(RuntimeError):TestClient(app).get('/api/_boom',headers=headers)
