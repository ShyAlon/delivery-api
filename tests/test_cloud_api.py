"""Actual routes against isolated real Postgres, with hermetic verified identities."""
import os,json
import uuid
import pytest
from fastapi.testclient import TestClient
from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope,tenant_context,session_root,TenantAccessDenied
from delivery_api.tenant_http import TenantMiddleware

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
if DSN and 'dbname=test_tenant_' not in DSN:
    raise RuntimeError('isolated test database required')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')
A=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
B=TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2')

@pytest.fixture
def cloud(monkeypatch,tmp_path,pg_workspace_factory,request):
    from contextlib import ExitStack
    stack=ExitStack()
    first=stack.enter_context(pg_workspace_factory());second=stack.enter_context(pg_workspace_factory())
    if request.module.__name__==__name__:
        monkeypatch.setattr(__import__(__name__),'A',first.scope)
        monkeypatch.setattr(__import__(__name__),'B',second.scope)
    import delivery_api.app as module
    from delivery_api import config
    store=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
    monkeypatch.setattr(module,'store',store)
    monkeypatch.setitem(module.app.state._state,'store',store)
    monkeypatch.setattr(module.orchestrator,'store',store)
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(module.bundles,'root',lambda:tmp_path)
    # Cloud dispatch runs behind the job-bound admission gateway; install the
    # test double so admitted routes exercise the seam (unadmitted stays 503).
    from types import SimpleNamespace as _NS
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'gateway_factory',
                        lambda:(lambda capability,job_id:_NS(job_id=job_id,retail_rates=None,
                                                             resolve_quote=lambda **kw:None,
                                                             measure_usage=lambda response:None,
                                                             resolve_serialized=None,measure_raw_usage=None)))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    from delivery_api.supabase_auth import SupabaseIdentityVerifier
    from pg_workspace import URL,Keys
    app=TenantMiddleware(module.app,verifier=SupabaseIdentityVerifier(URL,key_provider=Keys()),
        session_check=store.auth_session_active,request_database=store._postgres)
    # Don't start local legacy worker lifespan. Cloud mode must have no shared
    # startup data scans or automatic resume until scoped engine support lands.
    try:
        with TestClient(app) as client:yield client,store,tmp_path
    finally:stack.close()


def header(user):
    import time,jwt
    from pg_workspace import KEY,URL
    scope={'a':A,'b':B}[user];now=int(time.time())
    token=jwt.encode(dict(sub=scope.owner_id,session_id=scope.session_id,iat=now,
        exp=now+3600,iss=URL+'/auth/v1',aud='authenticated',role='authenticated'),KEY,algorithm='ES256')
    return {'Authorization':'Bearer '+token}


def test_task_api_cross_tenant_ids_events_feedback_cost(cloud):
    client,store,root=cloud
    title='api-'+uuid.uuid4().hex
    created=client.post('/api/tasks',json={'title':title},headers=header('a'))
    assert created.status_code==201,created.text
    task=created.json()['task_id']
    assert client.post('/api/tasks',json={'title':title},headers=header('b')).status_code==201
    for suffix in ['', '/events','/cost','/audit','/feedback','/artifacts.zip']:
        reply=client.get('/api/tasks/'+task+suffix,headers=header('b'))
        assert reply.status_code in (404,403), (suffix,reply.text)
    assert client.patch('/api/tasks/'+task,json={'title':'stolen'},headers=header('b')).status_code==404
    assert client.request('DELETE','/api/tasks/'+task,headers=header('b'),json={'confirm':True}).status_code==404
    assert client.get('/api/tasks').status_code==401
    health=client.get('/health').json()
    assert health['ok'] is True and set(health)<={'ok','runtime_id','repositories','built_at'}   # cloud mode adds the build id, its repository commit ids and the image build time (no tenant data) and nothing else


def test_session_registry_and_file_namespace(cloud):
    client,store,root=cloud
    sid=str(uuid.uuid4())
    with tenant_context(A):
        task=store.create_task(sid)
        store.set_engine_session(task['task_id'],sid)
        store.publish_task_revision_bytes(task['task_id'],[{'phase_key':'intent','name':'intent_model.json','data':b'{"problem_statement":"A private bundle"}'}],writer_instance='test-only')
    path=session_root(root,A,sid)
    (path/'interview').mkdir(parents=True)
    (path/'interview'/'intent_model.json').write_text('{"problem_statement":"A private bundle"}')
    # Unbound global legacy directories must never appear in cloud discovery.
    (root/'legacy'/'interview').mkdir(parents=True)
    (root/'legacy'/'interview'/'intent_model.json').write_text('{"problem_statement":"unbound"}')
    own=client.get('/api/sessions',headers=header('a'))
    assert own.status_code==200,own.text
    assert [row['session_id'] for row in own.json()['sessions']]==[sid]
    assert client.get('/api/sessions/'+sid,headers=header('a')).status_code==200
    assert client.get('/api/sessions/'+sid,headers=header('b')).status_code in (403,404)
    assert client.get('/api/sessions/legacy',headers=header('a')).status_code in (403,404)
    assert client.get('/api/sessions',headers=header('b')).json()['sessions']==[]
    outside=root/'outside.json';outside.write_text('{"secret":true}')
    (path/'interview'/'escape.json').symlink_to(outside)
    # A local symlink cannot affect the immutable PG publication.
    response=client.get('/api/sessions/'+sid,headers=header('a'))
    assert response.status_code==200 and 'A private bundle' in response.text
    assert 'secret' not in response.text


def test_unsafe_settings_local_auth_and_worker_routes_denied(cloud):
    client,_,_=cloud
    for path in ['/api/settings/artifacts-root','/api/settings/engine-python']:
        assert client.post(path,json={'path':'/tmp'},headers=header('a')).status_code==403
    for path in ['/auth/email-availability','/auth/github/start']:
        assert client.post(path,headers=header('a')).status_code==404
    for path in ['/api/tasks/guess/start','/api/tasks/guess/model-route','/api/simulations']:
        assert client.post(path,json={},headers=header('a')).status_code==503


def test_opened_gate_stack_decisions_keeps_auth_tenant_and_csrf_walls(cloud):
    """The route-wall exception for the founder stack-decision endpoint opens the
    path, never the ownership model: identity is still required, another tenant
    gets no existence confirmation, and cookie CSRF still applies."""
    client,store,root=cloud
    created=client.post('/api/tasks',json={'title':'stackwall-'+uuid.uuid4().hex},headers=header('a'))
    assert created.status_code==201,created.text
    task=created.json()['task_id']
    url='/api/tasks/'+task+'/gates/gate-architecture/stack-decisions'
    body={'revision':'0'*64,'decisions':[{'key':'language','text':'python'}]}
    # Cookie-authenticated POST without a matching Origin: CSRF wall first.
    client.cookies.set('requi_access',header('a')['Authorization'][7:])
    assert client.post(url,json=body).status_code==403
    client.cookies.clear()
    # No identity at all: 401, never the handler.
    assert client.post(url,json=body).status_code==401
    # Verified identity of another tenant: 404, byte-identical to a genuinely
    # nonexistent task - the response is no existence oracle.
    denied=client.post(url,json=body,headers=header('b'))
    missing=client.post(url.replace(task,'no-such-task'),json=body,headers=header('b'))
    assert denied.status_code==404 and denied.text==missing.text,(denied.status_code,denied.text,missing.status_code,missing.text)


def test_cloud_cookie_csrf_and_revoked_session(cloud):
    import psycopg
    client,store,root=cloud
    client.cookies.set('requi_access',header('a')['Authorization'][7:])
    assert client.get('/api/tasks').status_code==200
    assert client.post('/api/tasks',json={'title':'csrf-'+uuid.uuid4().hex}).status_code==403
    assert client.post('/api/tasks',json={'title':'csrf-'+uuid.uuid4().hex},headers={'Origin':'http://testserver'}).status_code==201
    assert client.post('/api/tasks',json={'title':'csrf-'+uuid.uuid4().hex},headers={'Origin':'https://evil.invalid'}).status_code==403


def test_downloaded_bytes_never_cross_session_owner_or_unbound_ref(cloud):
    client,store,base=cloud
    import io,zipfile
    sid=str(uuid.uuid4())
    with tenant_context(A):
        task=store.create_task(sid)
        store.set_engine_session(task['task_id'],sid)
        # A database artifact ref cannot override authorized filesystem namespace.
        store.add_task_artifact(task['task_id'],'intent','intent_model.json','/tmp/foreign.json')
    root=session_root(base,A,sid)
    (root/'interview').mkdir(parents=True)
    original=b'{"problem_statement":"private-A-bytes"}'
    (root/'interview'/'intent_model.json').write_bytes(original)
    task_id=task['task_id']
    with tenant_context(A):
        store.publish_task_revision_bytes(task_id,[{'phase_key':'intent','name':'intent_model.json','data':original}],writer_instance='test-only')
    own=client.get('/api/tasks/'+task_id+'/artifacts/intent/intent_model.json',headers=header('a'))
    assert own.status_code==200,own.text
    assert own.content==original
    other=client.get('/api/tasks/'+task_id+'/artifacts/intent/intent_model.json',headers=header('b'))
    assert other.status_code==404
    zipped=client.get('/api/tasks/'+task_id+'/artifacts.zip',headers=header('a'))
    assert zipped.status_code==200,zipped.text
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert original in [archive.read(name) for name in archive.namelist()]
    assert client.get('/api/tasks/'+task_id+'/artifacts.zip',headers=header('b')).status_code==404
    visible=client.get('/api/tasks/'+task_id,headers=header('a'))
    assert str(base) not in visible.text
    assert all('ref' not in row for row in visible.json()['artifacts'])


def test_owner_recovery_view_and_resume_admission_gate_leave_job_unchanged(cloud):
    client,store,root=cloud
    sid='recovery-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'phase:needs')
        store.claim_worker(job)
    own=client.get('/api/worker-jobs/recovery',headers=header('a'))
    assert own.status_code==200,own.text
    assert job in [row['job_id'] for row in own.json()['jobs']]
    other=client.get('/api/worker-jobs/recovery',headers=header('b'))
    assert job not in [row['job_id'] for row in other.json()['jobs']]
    endpoint='/api/tasks/'+task['task_id']+'/worker-resume'
    assert client.post(endpoint,json={'job_id':job,'phase':'needs'},headers=header('a')).status_code==503
    assert client.post(endpoint,json={'job_id':job,'phase':'needs'},headers=header('b')).status_code==404
    with tenant_context(A):
        assert store.worker_job(job)['state']=='running'
        assert store.worker_job(job)['attempt']==1
        store.delete_task(task['task_id'])


def test_resume_route_dispatches_through_seam_only_when_admitted(cloud,monkeypatch):
    import delivery_api.app as module
    client,store,root=cloud
    calls=[]
    async def fake_execute(task_id,phase,**kw):calls.append((task_id,phase))
    sid='resume-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        job=store.enqueue_worker(task['task_id'],'phase:needs')
        store.claim_worker(job);store.finish_worker(job,failed=True)
    endpoint='/api/tasks/'+task['task_id']+'/worker-resume';body={'job_id':job,'phase':'needs'}
    assert client.post(endpoint,json=body,headers=header('a')).status_code==503
    assert calls==[]
    with tenant_context(A):assert store.worker_job(job)['state']=='failed'
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    monkeypatch.setattr(module.orchestrator,'execute_phase',fake_execute)
    assert client.post(endpoint,json=body,headers=header('b')).status_code==404
    assert calls==[]
    assert client.post(endpoint,json={'job_id':job,'phase':'srs'},headers=header('a')).status_code==409
    ok=client.post(endpoint,json=body,headers=header('a'))
    assert ok.status_code==200,ok.text
    assert calls==[(task['task_id'],'needs')]
    with tenant_context(A):
        assert store.worker_job(job)['state']=='done'
        store.delete_task(task['task_id'])


def test_owner_recovers_committed_pending_retry_with_original_execution_options(cloud,monkeypatch):
    import delivery_api.app as module
    from delivery_api.orchestrator import ExecutionMode
    client,store,root=cloud
    sid='pending-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        for phase in ('intent','conops'):store.update_phase(tid,phase,'approved')
        store.update_phase(tid,'needs','failed')
        job=store.prepare_phase_retry(tid,'needs','owner','ollama:phi4',True)
    assert job in [j['job_id'] for j in client.get('/api/worker-jobs/recovery',headers=header('a')).json()['jobs']]
    assert job not in [j['job_id'] for j in client.get('/api/worker-jobs/recovery',headers=header('b')).json()['jobs']]
    endpoint='/api/tasks/'+tid+'/worker-resume';payload={'job_id':job,'phase':'needs'}
    assert client.post(endpoint,json=payload,headers=header('a')).status_code==503
    with tenant_context(A):assert store.worker_job(job)['state']=='pending'
    calls=[]
    async def execute(task_id,phase,**options):calls.append((task_id,phase,options))
    monkeypatch.setattr(module.orchestrator,'execute_phase',execute)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    assert client.post(endpoint,json=payload,headers=header('b')).status_code==404
    reply=client.post(endpoint,json=payload,headers=header('a'))
    assert reply.status_code==200,reply.text
    assert calls==[(tid,'needs',{'mode':ExecutionMode.RESUME,'model_route':'ollama:phi4'})]
    assert client.post(endpoint,json=payload,headers=header('a')).status_code==409
    with tenant_context(A):
        assert store.worker_job(job)['state']=='done'
        store.update_phase(tid,'needs','failed');store.delete_task(tid)


def test_gate_redo_route_closed_gate_then_owner_durable_dispatch(cloud,monkeypatch):
    import delivery_api.app as module
    from delivery_api.orchestrator import ExecutionMode
    client,store,root=cloud
    sid='gate-route-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        store.update_phase(tid,'needs','running');gate=store.wait_at_gate(tid,'needs')
        before=store.task(tid);events=store.events(tid)
    # The redo override names the local route, which cloud admits only under the
    # operator preview flag; this server has it (deployed cloud offers DeepSeek only).
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    url='/api/tasks/'+tid+'/gates/'+gate['gate_key']+'/decide'
    payload={'action':'redo','steer':'fix evidence','model_route':'ollama','model':'phi4','expected_revision':gate['revision']}
    assert client.post(url,json=payload,headers=header('b')).status_code==404
    assert client.post(url,json=payload,headers=header('a')).status_code==503
    with tenant_context(A):assert store.task(tid)==before and store.events(tid)==events
    calls=[]
    async def execute(task_id,phase,**options):calls.append((task_id,phase,options))
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    monkeypatch.setattr(module.orchestrator,'execute_phase',execute)
    reply=client.post(url,json=payload,headers=header('a'))
    assert reply.status_code==200,reply.text
    assert calls==[(tid,'needs',{'mode':ExecutionMode.REDO,'model_route':'ollama:phi4'})]
    with tenant_context(A):
        with store._postgres.transaction() as connection:
            jobs=connection.execute('SELECT * FROM worker_jobs WHERE task_id=%s',(tid,)).fetchall()
        assert len(jobs)==1 and jobs[0]['state']=='done'
        store.update_phase(tid,'needs','failed');store.delete_task(tid)


def test_cloud_rerun_preview_and_confirm_do_not_stale_session_generation(cloud,monkeypatch):
    import delivery_api.app as module
    client,store,root=cloud
    monkeypatch.setattr(module.config,'RUN_START_RATE_LIMIT',1000)
    sid='rerun-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
        before=store.task(tid);binding=store.session_binding(sid);audit=store.audit_entries(tid)
    url='/api/tasks/'+tid+'/rerun'
    preview=client.post(url,json={'from_phase':'needs','confirm':False},headers=header('a'))
    assert preview.status_code==409,preview.text
    assert preview.json()['detail']['code']=='tenant_rerun_unavailable'
    assert set(preview.json()['detail'])=={'code','error','cause','next'}
    assert 'No work changed' in preview.json()['detail']['next']
    assert client.post(url,json={'from_phase':'needs','confirm':False},headers=header('b')).status_code==404
    confirmed=client.post(url,json={'from_phase':'needs','confirm':True},headers=header('a'))
    assert confirmed.status_code==409
    assert confirmed.json()['detail']['code']=='tenant_rerun_unavailable'
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    assert client.post(url,json={'from_phase':'needs','confirm':True},headers=header('a')).status_code==409
    with tenant_context(A):
        assert store.task(tid)==before and store.session_binding(sid)==binding and store.audit_entries(tid)==audit
        with pytest.raises(TenantAccessDenied):store.prepare_rerun(tid,'needs')
        assert store.task(tid)==before
        store.delete_task(tid)


def test_cloud_provider_usage_categories_are_private_even_with_debug(cloud):
    import json
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('private-provider-audit-'+uuid.uuid4().hex)
        tid=task['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
    path=session_root(root,A,sid)/'interview';path.mkdir(parents=True)
    sensitive={'cache_hit_tokens':123,'cache_miss_tokens':456,'reasoning_tokens':789,'output_tokens':999}
    (path/'usage.jsonl').write_text(json.dumps(sensitive)+'\n')
    (path/'llm-calls.jsonl').write_text(json.dumps({'stage':'probing','usage':sensitive,'error':{'kind':'test','raw_response':sensitive}})+'\n')
    (path/'debug-http.jsonl').write_text(json.dumps({'response':sensitive})+'\n')
    for suffix in ['/execution','/execution?debug=true']:
        reply=client.get('/api/tasks/'+tid+suffix,headers=header('a'))
        assert reply.status_code==404,reply.text
        assert all(key not in reply.text for key in sensitive)
    debug=client.post('/api/tasks/'+tid+'/debug.zip',json={},headers=header('a'))
    assert debug.status_code==404 and all(key not in debug.text for key in sensitive)
    assert client.get('/api/tasks/'+tid+'/execution?debug=true',headers=header('b')).status_code==404
    assert client.post('/api/tasks/'+tid+'/debug.zip',json={},headers=header('b')).status_code==404
    assert json.loads((path/'usage.jsonl').read_text())==sensitive


def test_cloud_events_and_audit_never_project_provider_categories(cloud):
    client,store,root=cloud
    payload={'price':2,'price_usd':0.2,'usage':{'reasoning_tokens':9},'nested':[{'cache_hit_tokens':8,'output_tokens':7,'safe':'kept'}]}
    with tenant_context(A):
        task=store.create_task('usage-projection-'+uuid.uuid4().hex);tid=task['task_id']
        store.add_event(tid,'test','safe message',payload=payload)
        store.audit('operator','test',task_id=tid,detail=payload)
    for suffix in ['','/events','/audit']:
        reply=client.get('/api/tasks/'+tid+suffix,headers=header('a'))
        assert reply.status_code==200,reply.text
        assert 'reasoning_tokens' not in reply.text and 'cache_hit_tokens' not in reply.text and 'output_tokens' not in reply.text
        assert 'kept' in reply.text
    audit=client.get('/api/tasks/'+tid+'/audit',headers=header('a')).json()
    assert set(audit['totals'])=={'price'}
    assert 'price_usd' not in json.dumps(audit)
    assert audit['entries'][0]['detail']['price']==2
    with tenant_context(A):
        assert store.events(tid)[-1]['payload']==payload
        assert store.audit_entries(tid)[0]['detail']==payload


def test_cloud_packages_reject_incomplete_owner_and_hide_foreign_task(cloud):
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('private-export-'+uuid.uuid4().hex);tid=task['task_id']
    rejected=client.post('/api/tasks/'+tid+'/packages',headers=header('a'))
    assert rejected.status_code==409
    assert client.get('/api/tasks/'+tid+'/packages/'+'a'*64+'?generation=1',headers=header('a')).status_code==404
    for method,path in [('POST','/packages'),('GET','/packages/'+'a'*64+'?generation=1')]:
        assert client.request(method,'/api/tasks/'+tid+path,headers=header('b')).status_code==404


def test_raw_provider_audit_and_cache_categories_never_project_to_customer():
    from delivery_api.customer_projection import without_provider_usage,without_model_pricing
    audit={'raw_usage':{'prompt_tokens':4,'completion_tokens':2},'raw_usage_received_at':1,'usage':{'input_tokens':4},'payload':{'reasoning_tokens':2,'cache_hit_tokens':3,'model':'private','provider':'private','price_version':'private','safe':'customer label'}}
    projected=without_model_pricing(without_provider_usage(audit))
    assert projected=={'payload':{'safe':'customer label'}}


def test_stop_route_gate_closed_foreign_hidden_no_generation_change(cloud):
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('stop-api-'+uuid.uuid4().hex);tid=task['task_id'];before=task['generation']
    assert client.post(f'/api/tasks/{tid}/stop',headers=header('b')).status_code==404
    assert client.post(f'/api/tasks/{tid}/stop',headers=header('a')).status_code==503
    assert client.post(f'/api/tasks/{tid}/stop').status_code==401
    with tenant_context(A):
        assert store.task(tid)['generation']==before
        store.delete_task(tid)


def test_stop_route_hermetic_admitted_path_stays_pending_for_running_job(cloud,monkeypatch):
    import delivery_api.app as module
    from vmodel_contracts.interactive import EngineSnapshot,ProcessStatus,NextStep
    client,store,root=cloud;sid=str(uuid.uuid4());seen=[]
    with tenant_context(A):
        task=store.create_task('stop-route-'+uuid.uuid4().hex);tid=task['task_id'];store.set_engine_session(tid,sid)
        job=store.enqueue_worker(tid,'phase:needs');store.claim_worker(job)
    class Engine:
        def stop(self,session):
            seen.append(session)
            return EngineSnapshot(session_id=sid,task_id=tid,sequence=1,process_status=ProcessStatus.STOPPED,next_step=NextStep.STOP)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    monkeypatch.setattr(module,'engine_client',Engine())
    reply=client.post(f'/api/tasks/{tid}/stop',headers=header('a'))
    assert reply.status_code==200,reply.text
    assert reply.json()['state']=='stop_requested' and not reply.json()['delete_allowed']
    assert seen==[sid]
    with tenant_context(A):store.finish_stopped_worker(job,worker_quiescent=True)
    reply=client.post(f'/api/tasks/{tid}/stop',headers=header('a'))
    assert reply.status_code==200 and reply.json()['state']=='stopped'
    assert client.request('DELETE','/api/tasks/'+tid,headers=header('a'),json={'confirm':True}).status_code==200
    assert client.get('/api/tasks/'+tid,headers=header('a')).status_code==404


def test_stop_reconcile_route_gate_owner_and_durable_precondition(cloud,monkeypatch):
    import delivery_api.app as module
    from vmodel_contracts.interactive import EngineSnapshot,ProcessStatus,NextStep
    client,store,root=cloud;sid=str(uuid.uuid4());calls=[]
    with tenant_context(A):
        task=store.create_task('reconcile-route-'+uuid.uuid4().hex);tid=task['task_id'];store.set_engine_session(tid,sid)
    assert client.post(f'/api/tasks/{tid}/stop/reconcile',headers=header('b')).status_code==404
    assert client.post(f'/api/tasks/{tid}/stop/reconcile',headers=header('a')).status_code==503
    assert client.post(f'/api/tasks/{tid}/stop/reconcile').status_code==401
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    assert client.post(f'/api/tasks/{tid}/stop/reconcile',headers=header('a')).status_code==409
    class Engine:
        def reconcile_stop(self,session):
            calls.append(session)
            return EngineSnapshot(session_id=sid,task_id=tid,sequence=1,process_status=ProcessStatus.STOPPED,next_step=NextStep.STOP)
    monkeypatch.setattr(module,'engine_client',Engine())
    with tenant_context(A):store.request_task_stop(tid)
    reply=client.post(f'/api/tasks/{tid}/stop/reconcile',headers=header('a'))
    assert reply.status_code==200 and reply.json()['state']=='stopped' and calls==[sid]
    assert client.request('DELETE','/api/tasks/'+tid,headers=header('a'),json={'confirm':True}).status_code==200


def test_closed_gate_revision_draft_cloud_scoped_and_no_execution(cloud):
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('closed-draft-'+uuid.uuid4().hex);tid=task['task_id']
        store.wait_at_gate(tid,'needs');waiting=store.task(tid);rev='gate:retained-approval'
        store.retain_decision_review(tid,'gate:gate-needs',rev,{'task':waiting})
        store.decide_gate(tid,'gate-needs','approve',selected_option='Proceed');before=store.task(tid)
    url=f'/api/tasks/{tid}/gates/gate-needs/revision-drafts';body={'review_revision':rev,'edited_text':'new proposed wording'}
    assert client.post(url,json=body,headers=header('b')).status_code==404
    assert client.post(url,json=body).status_code==401
    reply=client.post(url,json=body,headers=header('a'))
    assert reply.status_code==200,reply.text
    assert reply.json()['dispatch_started'] is False and reply.json()['committed'] is False
    with tenant_context(A):
        assert store.task(tid)==before
        store.request_task_stop(tid);store.confirm_task_stopped(tid,engine_quiescent=True);store.delete_task(tid)


def test_orphan_operations_api_requires_admin_before_owner_view(cloud):
    client,store,root=cloud
    assert client.get('/api/admin/operations/orphaned-tasks',headers=header('a')).status_code==403
    assert client.get('/api/admin/operations/orphaned-tasks').status_code==401


def test_operations_workspace_api_requires_admin(cloud):
    client,_,_=cloud
    assert client.get('/api/admin/operations/workspace',headers=header('a')).status_code==403
    assert client.get('/api/admin/operations/workspace').status_code==401


def test_operator_api_unconfigured_is_closed_not_admin_default(cloud):
    client,_,_=cloud
    for path in ('/api/operator/sessions','/api/operator/credits','/api/operator/expense-totals','/api/operator/task-debug?task_id=fake','/api/operator/task-usage?task_id=fake','/api/operator/task-logs?task_id=fake','/api/operator/credit-account?owner_id=00000000-0000-0000-0000-000000000002'):
        assert client.get(path,headers=header('a')).status_code==503
        assert client.get(path,headers=header('a')).headers['cache-control']=='no-store'
        assert client.get(path).status_code==401
        assert client.post(path,json={},headers=header('a')).status_code==405


def test_operator_session_route_rechecks_sole_principal_and_registry(cloud,monkeypatch):
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client,store,_=cloud
    with tenant_context(B):
        tid=store.create_task('route-operator-'+uuid.uuid4().hex)['task_id'];sid='route-'+uuid.uuid4().hex;store.set_engine_session(tid,sid)
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_read TO delivery_app')
        c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'route-fake-v1',['sessions.read']))
    reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-fake-v1',frozenset({'sessions.read'})))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',reader)
    try:
        got=client.get('/api/operator/sessions',headers=header('a'))
        assert got.status_code==200 and any(row['session_id']==sid for row in got.json()['sessions'])
        assert client.get('/api/operator/sessions',headers=header('b')).status_code==404
        assert client.get('/api/operator/credits',headers=header('a')).status_code==404
        assert client.get('/api/operator/sessions?limit=1000',headers=header('a')).status_code==422
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
        assert client.get('/api/operator/sessions',headers=header('a')).status_code==404
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
        with tenant_context(B):store.delete_task(tid)


def test_operator_task_debug_route_rechecks_scope_and_never_exports_content(cloud,monkeypatch):
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client,store,_=cloud
    with tenant_context(B):tid=store.create_task('private-title-'+uuid.uuid4().hex)['task_id']
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_read TO delivery_app')
        c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'route-debug',['debug.read']))
    reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-debug',frozenset({'debug.read'})))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',reader)
    try:
        path='/api/operator/task-debug?task_id='+tid
        got=client.get(path,headers=header('a'))
        assert got.status_code==200 and got.json()['content']=='state_metadata_only'
        assert 'private-title' not in got.text and got.json()['logs_available'] is False
        assert client.get(path,headers=header('b')).status_code==404
        assert client.get('/api/operator/task-debug?task_id=missing',headers=header('a')).status_code==404
        assert client.get('/api/operator/task-debug?task_id='+'x'*241,headers=header('a')).status_code==422
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
        assert client.get(path,headers=header('a')).status_code==404
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
        with tenant_context(B):store.delete_task(tid)


def test_curated_operator_log_route_is_read_only_and_revocable(cloud,monkeypatch):
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client,store,_=cloud
    with tenant_context(B):tid=store.create_task('log-route-'+uuid.uuid4().hex)['task_id']
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_read TO delivery_app')
        c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'route-logs',['logs.read']))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-logs',frozenset({'logs.read'}))))
    try:
        path='/api/operator/task-logs?task_id='+tid
        got=client.get(path,headers=header('a'))
        assert got.status_code==200 and got.json()['content']=='curated_state_events_only'
        assert len(got.json()['events'])==1 and 'log-route' not in got.text
        assert client.get(path,headers=header('b')).status_code==404
        assert client.get(path+'&before_id=-1',headers=header('a')).status_code==422
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
        assert client.get(path,headers=header('a')).status_code==404
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
        with tenant_context(B):store.delete_task(tid)


def test_usage_route_has_own_capability_and_registry_revocation(cloud,monkeypatch):
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client,store,_=cloud
    with tenant_context(B):tid=store.create_task('usage-route-'+uuid.uuid4().hex)['task_id']
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_usage TO delivery_app')
        c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'route-usage',['usage.read']))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-usage',frozenset({'usage.read'}))))
    try:
        path='/api/operator/task-usage?task_id='+tid
        got=client.get(path,headers=header('a'))
        assert got.status_code==200 and got.json()['total_tokens']=='0' and got.json()['attempts']==0
        assert got.headers['cache-control']=='no-store'
        assert client.get(path,headers=header('b')).status_code==404
        assert client.get('/api/operator/task-debug?task_id='+tid,headers=header('a')).status_code==404
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
        assert client.get(path,headers=header('a')).status_code==404
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
        with tenant_context(B):store.delete_task(tid)


def test_suspended_file_only_gate_mutations_rejected_before_artifact_effect(cloud,monkeypatch):
    import psycopg,delivery_api.app as module
    client,store,root=cloud
    with tenant_context(A):task=store.create_task('suspended-file-'+uuid.uuid4().hex)
    touched=[]
    monkeypatch.setattr(module,'_gate_review',lambda *a: touched.append('review') or {})
    with psycopg.connect(DSN) as c:c.execute('INSERT INTO user_task_policy VALUES (%s,true,1,0)',(A.owner_id,))
    try:
        # semantic-review's cloud protocol gate stays closed (503). stack-decisions is
        # now an admitted file operation (the founder records decisions on the pickup
        # host): the route wall opens, and the suspended-tenant file guard itself
        # rejects before any artifact effect - translated to 404, no existence oracle.
        for suffix,body,expected in (('semantic-review',{'revision':'a'*64,'decisions':[]},503),
             ('stack-decisions',{'revision':'a'*64,'decisions':[{'key':'x','text':'y'}]},404)):
            response=client.post('/api/tasks/'+task['task_id']+'/gates/gate-srs/'+suffix,json=body,headers=header('a'))
            assert response.status_code==expected,(suffix,response.status_code,response.text)
        with tenant_context(A):
            with pytest.raises(TenantAccessDenied,match='suspended'):
                module.decide_semantic_gate(task['task_id'],'gate-srs',module.SemanticDecisions(revision='a'*64,decisions=[]),None)
            with pytest.raises(TenantAccessDenied,match='suspended'):
                module.decide_stack_gate(task['task_id'],'gate-srs',module.StackDecisions(revision='a'*64,decisions=[{'key':'x','text':'y'}]),None)
            with pytest.raises(TenantAccessDenied,match='suspended'):
                module._use_architecture_defaults(task['task_id'])
        assert touched==[]
        assert client.get('/api/tasks',headers=header('a')).status_code==200
        assert client.get('/api/tasks/'+task['task_id']+'/events',headers=header('a')).status_code==200
        assert client.get('/api/auth',headers=header('a')).status_code==200
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM user_task_policy WHERE owner_id=%s',(A.owner_id,))


def test_cloud_task_read_never_consults_shared_local_runner_or_exposes_paths(cloud,monkeypatch):
    import delivery_api.app as module
    client,store,root=cloud
    def shared_read(*a,**k):raise AssertionError('cloud read entered unscoped local runner')
    monkeypatch.setattr(module.runner,'input_state',shared_read)
    monkeypatch.setattr(module.runner,'work_state',shared_read)
    created=client.post('/api/tasks',json={'title':'private-read-'+uuid.uuid4().hex},headers=header('a'))
    assert created.status_code==201
    task_id=created.json()['task_id']
    for bound in (False,True):
        if bound:
            with tenant_context(A):store.set_engine_session(task_id,'isolated-'+uuid.uuid4().hex)
        response=client.get('/api/tasks/'+task_id,headers=header('a'))
        assert response.status_code==200,response.text
        body=response.json()
        assert body['active_work'] is None and body['artifact_projection']['root'] is None
        assert not body['input_state']['process_live']
        assert str(root) not in response.text
        assert client.get('/api/tasks/'+task_id,headers=header('b')).status_code==404


def test_cloud_bundle_reads_only_the_registered_session_and_hides_host_paths(cloud,monkeypatch):
    import delivery_api.app as module
    client,store,root=cloud
    sid='owner-read-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
        store.publish_task_revision_bytes(task['task_id'],[{'phase_key':'intent','name':'intent_model.json','data':b'{"problem_statement":"My bundle"}'}],writer_instance='test-only')
    path=session_root(root,A,sid)
    def global_scan(*a,**k):raise AssertionError('cloud read scanned a shared or sibling directory')
    monkeypatch.setattr(module.bundles,'discover',global_scan)
    monkeypatch.setattr(module.bundles,'_discover',global_scan)
    listing=client.get('/api/sessions',headers=header('a'))
    assert listing.status_code==200,listing.text
    assert listing.json()['sessions'][0]['title']=='My bundle'
    assert 'path' not in listing.json()['sessions'][0]
    bundle=client.get('/api/sessions/'+sid,headers=header('a'))
    assert bundle.status_code==200,bundle.text
    assert bundle.json()['session']['title']=='My bundle'
    assert 'path' not in bundle.json()['session']
    assert str(root) not in listing.text+bundle.text
    assert client.get('/api/sessions/'+sid,headers=header('b')).status_code==404
    assert client.get('/api/sessions',headers=header('b')).json()['sessions']==[]

def test_cloud_question_quality_settings_cannot_call_shared_engine(cloud,monkeypatch):
    import delivery_api.app as module
    client,_,_=cloud
    def shared_read(*a,**k):raise AssertionError('customer reached global engine configuration')
    monkeypatch.setattr(module.engine_client,'question_quality_config',shared_read)
    assert client.get('/api/question-quality-config',headers=header('a')).status_code==404


def test_cloud_download_rejects_symlink_swap_after_path_validation(cloud,monkeypatch):
    import delivery_api.app as module
    client,store,base=cloud
    sid='download-race-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
    root=session_root(base,A,sid);folder=root/'interview';folder.mkdir(parents=True)
    artifact=folder/'intent_model.json';artifact.write_text('{"problem_statement":"private"}')
    outside=base/'foreign.json';outside.write_text('foreign-secret-bytes')
    original=module._task_artifact
    def swap(*args):
        path=original(*args);artifact.unlink();artifact.symlink_to(outside);return path
    monkeypatch.setattr(module,'_task_artifact',swap)
    response=client.get('/api/tasks/'+tid+'/artifacts/intent/intent_model.json',headers=header('a'))
    assert response.status_code==404,response.text
    assert 'foreign-secret-bytes' not in response.text


def test_owner_read_rechecks_revocation_after_opening_file(cloud,monkeypatch):
    import delivery_api.tenant_files as files
    client,store,base=cloud
    sid='revoke-read-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
    root=session_root(base,A,sid);root.mkdir(parents=True)
    (root/'output.txt').write_text('private bytes')
    original=files.authorized_session;calls=[]
    def revoke(*args,**kwargs):
        calls.append(1)
        if len(calls)>1:raise TenantAccessDenied('revoked after open')
        return original(*args,**kwargs)
    monkeypatch.setattr(files,'authorized_session',revoke)
    with tenant_context(A),pytest.raises(TenantAccessDenied):files.read_session_bytes(base,sid,'output.txt',store)

def test_read_rejects_replaced_directory_namespace(cloud,monkeypatch):
    import delivery_api.tenant_files as files
    import os
    client,store,base=cloud
    sid='namespace-read-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);store.set_engine_session(task['task_id'],sid)
    root=session_root(base,A,sid);folder=root/'interview';folder.mkdir(parents=True)
    (folder/'output.txt').write_text('original')
    original=os.fdopen
    def swap(fd,*args,**kwargs):
        folder.rename(root/'detached');folder.mkdir();(folder/'output.txt').write_text('replacement')
        return original(fd,*args,**kwargs)
    monkeypatch.setattr(os,'fdopen',swap)
    with tenant_context(A),pytest.raises(TenantAccessDenied):files.read_session_bytes(base,sid,'interview/output.txt',store)

def test_cloud_startup_skips_all_legacy_recovery_and_dispatch(cloud,monkeypatch):
    import asyncio
    import delivery_api.app as module
    client,store,base=cloud
    async def forbidden(*args,**kwargs):raise AssertionError('legacy recovery/dispatch during cloud startup')
    def forbidden_sync(*args,**kwargs):raise AssertionError('legacy simulation recovery during cloud startup')
    monkeypatch.setattr(module,'_recover_tasks',forbidden)
    monkeypatch.setattr(module,'_catch_up_interview_progress',forbidden)
    monkeypatch.setattr(module.jobs,'start',forbidden)
    monkeypatch.setattr(module.simulation,'reconcile_interrupted',forbidden_sync)
    async def run():
        async with module.lifespan(module.app):pass
    asyncio.run(run())
    assert module.cloud_execution_admitted() is False


def test_cloud_gate_artifact_reference_never_exposes_host_path(cloud):
    client,store,base=cloud
    sid='gate-safe-'+uuid.uuid4().hex
    with tenant_context(A):
        task=store.create_task(sid);tid=task['task_id'];store.set_engine_session(tid,sid)
        store.update_phase(tid,'needs','running');store.wait_at_gate(tid,'needs')
    root=session_root(base,A,sid);(root/'spec').mkdir(parents=True)
    (root/'spec'/'needs.json').write_text('{}')
    response=client.get('/api/tasks/'+tid,headers=header('a'))
    assert response.status_code==200,response.text
    assert response.json()['control_snapshot']['artifact_ref'] is None
    assert str(base) not in response.text


def test_owner_classifications_expose_differences_without_cross_tenant_access(cloud):
    client,store,root=cloud
    sid=str(uuid.uuid4())
    q,a='Which view?','A calm view'
    with tenant_context(A):
        tid=store.create_task('Owner classifications')['task_id']
        store.set_engine_session(tid,sid)
        store.add_event(tid,'waiting_input',q,phase_key='intent',payload={'question':q,'waiting_input':True})
        store.add_event(tid,'answer','You: '+a,phase_key='intent',payload={'answer':a})
    folder=session_root(root,A,sid)/'interview';folder.mkdir(parents=True)
    record={'slot':'coverage','timestamp':'test','input':{'state':f'Question: {q}\nAnswer: {a}'},
            'baseline':{'status':'ok','verdict':{'relevance':'relevant'}},
            'jev':{'status':'ok','verdict':{'relevance':'thin'}}}
    from test_http_cutover import _PGUsage
    with tenant_context(A):
        _PGUsage(store,tid,'interview/categorization-shadow.jsonl').write_text(json.dumps(record)+'\n')
    # A conflicting local copy must never win over the owned PG document.
    (folder/'categorization-shadow.jsonl').write_text('untrusted local residue\n')
    url=f'/api/tasks/{tid}/classifications'
    own=client.get(url,headers=header('a'))
    assert own.status_code==200,own.text
    answer=next(iter(own.json()['answers'].values()))
    assert answer['coverage']['differed'] is True
    assert answer['coverage']['jev']=={'relevance':'thin'}
    assert client.get(url,headers=header('b')).status_code in (403,404)
    assert client.get(url).status_code==401


def test_pg_classifications_missing_or_corrupt_never_fall_back_to_local_or_leak(cloud,monkeypatch):
    client,store,root=cloud
    from test_http_cutover import _PGUsage
    from delivery_api.generator_documents import GeneratorDocuments
    sid=str(uuid.uuid4())
    with tenant_context(A):
        tid=store.create_task('Bound classifications')['task_id'];store.set_engine_session(tid,sid)
    folder=session_root(root,A,sid)/'interview';folder.mkdir(parents=True)
    (folder/'categorization-shadow.jsonl').write_text('{"private":"local residue"}\n')
    url=f'/api/tasks/{tid}/classifications'
    got=client.get(url,headers=header('a'))
    assert got.status_code==200 and got.json()['answers']=={} and got.json()['reason']=='no shadow evidence'
    with tenant_context(A):_PGUsage(store,tid,'interview/categorization-shadow.jsonl').write_text('private malformed bytes\n')
    got=client.get(url,headers=header('a'))
    assert got.status_code==503 and 'private' not in got.text
    def broken(*a,**kw):raise ConnectionError('postgresql://private-password@host/db')
    monkeypatch.setattr(GeneratorDocuments,'read',broken)
    got=client.get(url,headers=header('a'))
    assert got.status_code==503 and 'private-password' not in got.text
    assert client.get(url,headers=header('b')).status_code in (403,404)


@pytest.mark.parametrize('record',[
    {'slot':'coverage','input':[]},
    {'slot':'coverage','input':{'state':'x'},'baseline':'private'},
    {'slot':'coverage','input':{'state':'x'},'baseline':{'verdict':'private'}},
    {'slot':{},'input':{'state':'x'}},
    {'slot':'coverage','input':{'state':'x'},'jev':{'status':{'private':'bad'}}},
    {'slot':'coverage','input':{'state':'x'},'jev':{'verdict':{'private':float('nan')}}},
])
def test_pg_classification_malformed_nested_records_are_unavailable(cloud,record):
    client,store,root=cloud
    from test_http_cutover import _PGUsage
    with tenant_context(A):
        tid=store.create_task('Malformed shadow')['task_id'];store.set_engine_session(tid,str(uuid.uuid4()))
        _PGUsage(store,tid,'interview/categorization-shadow.jsonl').write_text(json.dumps(record)+'\n')
    got=client.get(f'/api/tasks/{tid}/classifications',headers=header('a'))
    assert got.status_code==503 and 'unreadable' in got.text
    assert 'private' not in got.text


def test_pg_classifications_reject_response_amplification(cloud,monkeypatch):
    client,store,root=cloud
    import importlib
    from test_http_cutover import _PGUsage
    turns=[{'q':f'question-{i}', 'a':f'answer-{i}'} for i in range(10)]
    monkeypatch.setattr(importlib.import_module('delivery_api.app'),'_qa_turns',lambda events:turns)
    record={'slot':'coverage','input':{'state':'\n'.join(t['q']+'\n'+t['a'] for t in turns)},
        'jev':{'status':'ok','verdict':{'reason':'x'*(1024*1024)}}}
    with tenant_context(A):
        tid=store.create_task('Bounded projection')['task_id'];store.set_engine_session(tid,str(uuid.uuid4()))
        _PGUsage(store,tid,'interview/categorization-shadow.jsonl').write_text(json.dumps(record)+'\n')
    got=client.get(f'/api/tasks/{tid}/classifications',headers=header('a'))
    assert got.status_code==503
    assert len(got.content)<1024


def test_pg_classifications_reject_excessive_join_work(cloud,monkeypatch):
    client,store,root=cloud
    import importlib
    from test_http_cutover import _PGUsage
    monkeypatch.setattr(importlib.import_module('delivery_api.app'),'_qa_turns',lambda events:[{'q':'q','a':'a'}]*10000)
    record={'slot':'coverage','input':{'state':'x'*7000}}
    with tenant_context(A):
        tid=store.create_task('Bounded join')['task_id'];store.set_engine_session(tid,str(uuid.uuid4()))
        _PGUsage(store,tid,'interview/categorization-shadow.jsonl').write_text(json.dumps(record)+'\n')
    got=client.get(f'/api/tasks/{tid}/classifications',headers=header('a'))
    assert got.status_code==503
    assert 'read limits' in got.text


def test_usage_missing_permission_explained_only_to_verified_debug_operator(cloud,monkeypatch):
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client,store,_=cloud
    with tenant_context(A):tid=store.create_task('usage-config-reason')['task_id']
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_read TO delivery_app')
        c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'usage-config',['debug.read']))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'usage-config',frozenset({'debug.read'}))))
    try:
        path='/api/operator/task-usage?task_id='+tid
        got=client.get(path,headers=header('a'))
        assert got.status_code==200 and got.json()['availability']=='unavailable'
        assert got.json()['required_capability']=='usage.read'
        assert got.json()['category']=='permission_missing'
        assert client.get(path,headers=header('b')).status_code==404
        with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
        assert client.get(path,headers=header('a')).status_code==404
    finally:
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
        with tenant_context(A):store.delete_task(tid)


def test_delete_requires_exact_confirmation_then_stop_hides_running_task(cloud):
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('confirm-delete-'+uuid.uuid4().hex);tid=task['task_id']
        store.set_engine_session(tid,'delete-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'phase:needs');store.claim_worker(job)
        generation=store.task(tid)['generation']
    url='/api/tasks/'+tid
    assert client.delete(url,headers=header('a')).status_code==422
    for body,status in [({},409),({'confirm':False},409),({'confirm':'true'},422)]:
        assert client.request('DELETE',url,headers=header('a'),json=body).status_code==status
    with tenant_context(A):assert store.task(tid)['generation']==generation
    assert client.request('DELETE',url,headers=header('b'),json={'confirm':True}).status_code==404
    out=client.request('DELETE',url,headers=header('a'),json={'confirm':True})
    assert out.status_code==200,out.text
    assert out.json()['hidden'] and out.json()['stop_requested'] and out.json()['accounting_retained']
    assert not out.json()['worker_exit_verified'] and not out.json()['accounting_settled']
    assert client.get(url,headers=header('a')).status_code==404
    with tenant_context(A):
        assert store.worker_job(job)['state']=='running'
        assert store.delete_task(tid,confirm=True)['already_hidden']
