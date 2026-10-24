"""The wall in front of the legacy shared-worker routes: closed unless the in-process engine is installed, and even then only
interview start and answer are reopened. Hermetic: no database, no model, no network."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from delivery_api import cloud_runtime
from delivery_api.tenant_http import TenantMiddleware, _interview_route_open
from delivery_api.tenancy import TenantScope

U1='00000000-0000-0000-0000-000000000001'
OPENED=('/api/tasks/t1/start','/api/tasks/t1/answer')
STILL_BLOCKED=('/api/tasks/t1/advance','/api/tasks/t1/respond','/api/tasks/t1/route','/api/tasks/t1/model-route','/api/tasks/t1/decision',
               '/api/tasks/t1/requirements/architecture/resolve','/api/tasks/t1/gates/intent/redo','/api/tasks/t1/simulations',
               '/api/tasks/t1/simulate','/runs','/links','/api/engine/x','/api/sessions/s1/answer')


class Verifier:
    def verify(self,token):return TenantScope(U1,U1)


def client():
    app=FastAPI()
    for path in OPENED+STILL_BLOCKED:
        app.add_api_route(path,lambda:{'reached':True},methods=['POST'])
    app.add_middleware(TenantMiddleware,verifier=Verifier())
    return TestClient(app,raise_server_exceptions=False)


def post(c,path):return c.post(path,headers={'Authorization':'Bearer a'})


def test_every_route_stays_blocked_while_the_engine_is_not_installed(monkeypatch):
    monkeypatch.setattr(cloud_runtime,'_state',None)
    with client() as c:
        for path in OPENED+STILL_BLOCKED:
            r=post(c,path)
            assert r.status_code==503 and r.json()['detail']=='Cloud execution pending scoped worker protocol',path


def test_only_start_and_answer_open_when_the_engine_is_installed(monkeypatch):
    monkeypatch.setattr(cloud_runtime,'_state',object())
    with client() as c:
        for path in OPENED:
            r=post(c,path)
            assert r.status_code==200 and r.json()=={'reached':True},path
        for path in STILL_BLOCKED:
            assert post(c,path).status_code==503,path


def test_gate_decide_family_stays_open_for_session_file_operations(monkeypatch):
    """Gate-scoped session file operations (decide, revision drafts, founder stack
    decisions) run under the task-operation guard on any admitted host; the cloud
    wall must not strand a founder decision on the pickup host."""
    monkeypatch.setattr(cloud_runtime,'_state',None)
    with client() as c:
        for path in ('/api/tasks/t1/gates/g1/decide','/api/tasks/t1/gates/g1/revision-drafts',
                     '/api/tasks/t1/gates/gate-architecture/stack-decisions'):
            app_routes=[r for r in c.app.routes if getattr(r,'path',None)==path]
            if not app_routes:
                c.app.add_api_route(path,lambda:{'reached':True},methods=['POST'])
            r=post(c,path)
            assert r.status_code==200 and r.json()=={'reached':True},path
        assert post(c,'/api/tasks/t1/gates/g1/redo').status_code==503


def test_an_opened_route_still_needs_a_signed_in_user(monkeypatch):
    monkeypatch.setattr(cloud_runtime,'_state',object())
    with client() as c:
        assert c.post('/api/tasks/t1/start').status_code==401


@pytest.mark.parametrize('path,expected',[('/api/tasks/abc/start',True),('/api/tasks/abc/answer',True),('/api/tasks/abc/advance',False),
    ('/api/tasks/abc/start/x',False),('/api/tasks//start',False),('/start',False),('/api/sessions/abc/start',False)])
def test_route_match_is_exact(monkeypatch,path,expected):
    monkeypatch.setattr(cloud_runtime,'_state',object())
    assert _interview_route_open(path) is expected
