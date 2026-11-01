"""Retired force-job closure cannot mutate through stale browser/service routes."""
from pathlib import Path
from fastapi.testclient import TestClient
from delivery_api.backoffice import create_app
from delivery_api.tenancy import tenant_context


def test_backoffice_has_no_force_controls_or_forwarding():
    calls=[]
    import httpx
    def upstream(request):calls.append(request);return httpx.Response(200,json={})
    client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    assert client.get('/api/operations/force-stop-plan',headers={'Authorization':'Bearer fake'}).status_code==404
    assert client.post('/api/operations/force-stop-all',json={},headers={'Authorization':'Bearer fake','Origin':'http://testserver'}).status_code in (404,405)
    assert calls==[]
    root=Path(__file__).parents[1]/'src/delivery_api/backoffice_web'
    assert 'force-stop-review' not in (root/'index.html').read_text()
    assert 'reviewForceStop' not in (root/'backoffice.js').read_text()


def test_stale_service_force_routes_return_retired_without_mutation(pg_workspace_factory,pg_task_app,monkeypatch):
    from delivery_api import app as service
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        tid=workspace.store.create_task('retired force route')['task_id']
        before=workspace.store.task(tid)
        monkeypatch.setattr(service,'_require_account_debug',lambda:None)
        client=TestClient(pg_task_app(workspace))
        assert client.get('/api/operations/force-stop-plan',headers=workspace.headers).status_code==410
        result=client.post('/api/operations/force-stop-all',json={'fingerprint':'a'*64,'confirmation':'FORCE STOP 1 JOBS'},headers=workspace.headers)
        assert result.status_code==410 and 'No job or accounting record changed' in result.text
        assert workspace.store.task(tid)==before
