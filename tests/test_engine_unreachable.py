"""Engine-unreachable failures surface as actionable 503s, never raw 500s."""
import httpx,pytest
from fastapi.testclient import TestClient
from vmodel_contracts.interactive import *
import delivery_api.app as module
from delivery_api.engine_client import EngineUnreachableError,HttpEngineClient

@pytest.fixture
def failure_workspace(pg_scoped_workspace,monkeypatch):
 # Preserve the original local route contracts, not cloud execution admission.
 monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
 return pg_scoped_workspace


def test_transport_failure_becomes_unreachable_error():
 def boom(request):raise httpx.ConnectError("connection refused",request=request)
 client=HttpEngineClient('http://engine',transport=httpx.MockTransport(boom))
 with pytest.raises(EngineUnreachableError) as caught:
  client.start(StartSessionRequest(task_id='t',idea='idea',model_route='m'))
 assert caught.value.base_url=='http://engine'

class DownEngine:
 def preview_route(self,route):raise EngineUnreachableError('http://127.0.0.1:8010',httpx.ConnectError('refused'))
 def start(self,request):raise EngineUnreachableError('http://127.0.0.1:8010',httpx.ConnectError('refused'))
 def snapshot(self,sid):raise EngineUnreachableError('http://127.0.0.1:8010',httpx.ConnectError('refused'))
 def submit(self,sid,response):raise EngineUnreachableError('http://127.0.0.1:8010',httpx.ConnectError('refused'))

def test_start_with_engine_down_is_actionable_503(tmp_path,monkeypatch,failure_workspace):
 store=failure_workspace.store;task=store.create_task('Dungeon master','A virtual dungeon master')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',DownEngine())
 response=TestClient(module.app).post(f"/api/tasks/{task['task_id']}/start",json={'model_route':'ollama'})
 assert response.status_code==503
 detail=response.json()['detail']
 assert detail['error']=="The interview engine is not reachable."
 assert '8010' in detail['cause']
 assert 'uvicorn interview_engine.http_service:app' in detail['next']
 events=store.events(task['task_id'])
 assert not events  # Route preview fails before a session is created.

def test_answer_with_engine_down_is_actionable_503(tmp_path,monkeypatch,failure_workspace):
 store=failure_workspace.store;task=store.create_task('Dungeon master','brief')
 store.set_engine_session(task['task_id'],'failure-'+failure_workspace.scope.owner_id);store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',DownEngine())
 response=TestClient(module.app).post(f"/api/tasks/{task['task_id']}/answer",json={'answer':'1'})
 assert response.status_code==503
 assert 'not reachable' in response.json()['detail']['error']

def test_view_page_renders_when_engine_down(tmp_path,monkeypatch,failure_workspace):
 store=failure_workspace.store;task=store.create_task('Dungeon master','brief')
 store.set_engine_session(task['task_id'],'failure-'+failure_workspace.scope.owner_id);store.update_phase(task['task_id'],'intent','running')
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module,'engine_client',DownEngine())
 body=TestClient(module.app).get(f"/api/tasks/{task['task_id']}").json()
 assert body['input_state']['process_live'] is False
 assert body['input_state']['waiting'] is False
