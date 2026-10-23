import httpx,pytest
from fastapi.testclient import TestClient
from delivery_api.backoffice import settings,create_app


def test_private_listener_defaults_and_configurable_ports():
 assert settings({})==('127.0.0.1',8020,'http://127.0.0.1:8000')
 assert settings({'DELIVERY_PORT':'9000','BACKOFFICE_PORT':'9020'})==('127.0.0.1',9020,'http://127.0.0.1:9000')

@pytest.mark.parametrize('change',[{'BACKOFFICE_HOST':'0.0.0.0'},{'BACKOFFICE_PORT':'8000'},{'BACKOFFICE_PORT':'8010'},
 {'BACKOFFICE_PORT':'bad'},{'BACKOFFICE_SERVICE_URL':'http://evil.invalid'},
 {'BACKOFFICE_SERVICE_URL':'http://user:pass@localhost:8000'},{'BACKOFFICE_SERVICE_URL':'http://localhost:8000/path'}])
def test_unreviewed_exposure_collision_or_service_origin_rejected(change):
 with pytest.raises(ValueError):settings(change)


def test_proxy_preserves_service_identity_checks_and_has_no_writes():
 calls=[]
 def upstream(request):
  calls.append(request)
  return httpx.Response(403,json={'detail':'admin required'})
 client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 assert client.get('/health').json()['role']=='backoffice'
 assert client.get('/api/operations/workspace').status_code==401 and calls==[]
 reply=client.get('/api/operations/workspace',headers={'Authorization':'Bearer fake'})
 assert reply.status_code==403 and reply.headers['cache-control']=='no-store'
 assert calls[0].headers['authorization']=='Bearer fake' and calls[0].url.path=='/api/admin/operations/workspace'
 assert client.get('/api/operations/unknown',headers={'Authorization':'Bearer fake'}).status_code==404
 assert client.post('/api/operations/workspace',json={},headers={'Authorization':'Bearer fake'}).status_code==405
 assert len(calls)==1

@pytest.mark.parametrize('mode',['redirect','oversize','invalid'])
def test_proxy_never_follows_redirect_or_accepts_unbounded_invalid_data(mode):
 def upstream(request):
  if mode=='redirect':return httpx.Response(302,headers={'Location':'http://evil.invalid'})
  return httpx.Response(200,content=b'x'*(1024*1024+1) if mode=='oversize' else b'[]')
 client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 assert client.get('/api/operations/orphaned-tasks',headers={'Authorization':'Bearer fake'}).status_code==502


def test_curated_log_proxy_rejects_raw_and_destination_parameters():
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(503,json={'detail':'registry unavailable'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 headers={'Authorization':'Bearer fake'}
 assert c.get('/api/operations/task-logs?task_id=fake&before_id=12&limit=10',headers=headers).status_code==503
 assert calls[0].url.path=='/api/operator/task-logs' and calls[0].url.params['before_id']=='12'
 for query in ('task_id=fake&raw=true','task_id=fake&destination=elsewhere','task_id=a&task_id=b'):
  assert c.get('/api/operations/task-logs?'+query,headers=headers).status_code==422
 assert len(calls)==1


def test_task_usage_proxy_preserves_authority_and_only_exact_task_key():
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(503,json={'detail':'unconfigured'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 h={'Authorization':'Bearer fake'}
 assert c.get('/api/operations/task-usage?task_id=fake',headers=h).status_code==503
 assert calls[0].url.path=='/api/operator/task-usage'
 assert c.get('/api/operations/task-usage?task_id=fake&raw=true',headers=h).status_code==422
 assert len(calls)==1


def test_local_operator_routes_are_absent_before_auth_and_no_store(monkeypatch):
 from delivery_api import app as service,config
 monkeypatch.setattr(config,'CLOUD_TENANTS',False)
 client=TestClient(service.app)
 for path in ('/api/operator/sessions','/api/operator/credits','/api/operator/task-debug?task_id=x','/api/operator/task-logs?task_id=x','/api/operator/expense-totals','/api/operator/payment-journal?start_at=1&end_at=2','/api/operator/payment-totals?start_at=1&end_at=2'):
  for method in ('GET','POST'):
   got=client.request(method,path)
   assert got.status_code==404 and got.headers['cache-control']=='no-store'


def test_vendor_status_is_proxied_to_the_operator_route_and_absent_locally(monkeypatch):
 calls=[]
 def upstream(request):calls.append(request);return httpx.Response(503,json={'detail':'registry unavailable'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 assert c.get('/api/operations/vendor-status',headers={'Authorization':'Bearer fake'}).status_code==503
 assert calls[0].url.path=='/api/operator/vendor-status'
 from delivery_api import app as service,config
 monkeypatch.setattr(config,'CLOUD_TENANTS',False)
 got=TestClient(service.app).get('/api/operator/vendor-status')
 assert got.status_code==404 and got.headers['cache-control']=='no-store'


def test_payment_read_proxy_only_bounded_known_parameters_and_no_writes():
 calls=[]
 def upstream(request):calls.append(request);return httpx.Response(503,json={'detail':'registry unavailable'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 h={'Authorization':'Bearer fake'}
 assert c.get('/api/operations/payment-totals?start_at=1&end_at=2',headers=h).status_code==503
 assert calls[0].url.path=='/api/operator/payment-totals'
 assert c.get('/api/operations/payment-journal?start_at=1&end_at=2&limit=1',headers=h).status_code==503
 assert c.get('/api/operations/payment-journal?start_at=1&end_at=2&raw=true',headers=h).status_code==422
 assert c.post('/api/operations/payment-journal',headers=h).status_code==405
 assert len(calls)==2

def test_attempt_releases_proxy_preserves_authority_and_rejects_unknown_keys():
 calls=[]
 def upstream(request):
  calls.append(request);return httpx.Response(503,json={'detail':'unconfigured'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 h={'Authorization':'Bearer fake'}
 assert c.get('/api/operations/attempt-releases?limit=50',headers=h).status_code==503
 assert calls[0].url.path=='/api/admin/operations/attempt-releases'
 assert calls[0].url.params.get('limit')=='50'
 assert c.get('/api/operations/attempt-releases?raw=true',headers=h).status_code==422
 assert c.get('/api/operations/attempt-releases').status_code==401
 assert len(calls)==1
