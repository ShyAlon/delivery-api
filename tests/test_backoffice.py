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


def test_operator_routes_still_require_identity_when_retired_mode_is_flipped(monkeypatch):
 from delivery_api import app as service,config
 monkeypatch.setattr(config,'CLOUD_TENANTS',False)
 client=TestClient(service.app)
 for path in ('/api/operator/sessions','/api/operator/credits','/api/operator/task-debug?task_id=x','/api/operator/task-logs?task_id=x','/api/operator/expense-totals','/api/operator/payment-journal?start_at=1&end_at=2','/api/operator/payment-totals?start_at=1&end_at=2'):
  for method in ('GET','POST'):
   got=client.request(method,path)
   assert got.status_code==401


def test_vendor_status_is_proxied_to_the_operator_route_and_absent_locally(monkeypatch):
 calls=[]
 def upstream(request):calls.append(request);return httpx.Response(503,json={'detail':'registry unavailable'})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 assert c.get('/api/operations/vendor-status',headers={'Authorization':'Bearer fake'}).status_code==503
 assert calls[0].url.path=='/api/operator/vendor-status'
 from delivery_api import app as service,config
 monkeypatch.setattr(config,'CLOUD_TENANTS',False)
 got=TestClient(service.app).get('/api/operator/vendor-status')
 assert got.status_code==401


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


def test_acceptance_readiness_reuses_shared_checks_and_exposes_no_private_ids():
 calls=[]
 def upstream(request):
  calls.append((request.method,request.url.path))
  if request.url.path=='/api/auth':return httpx.Response(200,json={'authenticated':True,'mode':'supabase'})
  if request.url.path=='/api/operations/test-tasks':return httpx.Response(200,json={'tasks':[{'task_id':'private-task-id'}]})
  return httpx.Response(200,json={'pins':[],'history':[{'operator_subject':'private-owner-id'}]})
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
 assert c.get('/api/operations/acceptance-readiness').status_code==401
 response=c.get('/api/operations/acceptance-readiness',headers={'Authorization':'Bearer fixture'})
 assert response.status_code==200 and response.headers['cache-control']=='no-store'
 body=response.json();assert body['owner_only'] and not body['activation_enabled'] and not body['ready']
 assert 'private-' not in response.text
 assert {x['check'] for x in body['checks']}=={'session','target','token/session','marker migration','default pin','ceiling'}
 assert all(method=='GET' for method,path in calls)
 assert c.post('/api/operations/acceptance-readiness',headers={'Authorization':'Bearer fixture'}).status_code==405


def test_acceptance_readiness_requires_reviewed_operator_access():
 c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(lambda request:httpx.Response(403,json={'detail':'private sql'}))))
 r=c.get('/api/operations/acceptance-readiness',headers={'Authorization':'Bearer fixture'})
 assert r.status_code==403 and 'private sql' not in r.text


def test_build_projection_uses_running_service_and_drops_noncommit_health_fields():
    calls=[]
    def upstream(request):
        calls.append(request)
        return httpx.Response(200,json={'repositories':{'delivery-api':'a'*40,'interview-engine':'b'*40,'spec-generator':'c'*40,'bad':'not-commit'},'total_spend_usd':100,'tasks':4})
    client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    response=client.get('/api/operations/build',headers={'Authorization':'Bearer fake'})
    assert response.status_code==200
    assert response.json()=={'repositories':{'delivery-api':'a'*40,'interview-engine':'b'*40}}
    assert calls[0].url.path=='/health'
