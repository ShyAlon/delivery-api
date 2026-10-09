"""Actual routes against isolated real Postgres, with hermetic verified identities."""
import os
from pathlib import Path
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
A=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001')
B=TenantScope('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002')

class Verifier:
    def verify(self,token):
        if token not in ('a','b'):raise TenantAccessDenied('bad')
        return A if token=='a' else B

@pytest.fixture
def cloud(monkeypatch,tmp_path):
    import delivery_api.app as module
    from delivery_api import config
    store=PostgresStore(DSN.replace('user=sandbox','user=delivery_app'))
    monkeypatch.setattr(module,'store',store)
    monkeypatch.setattr(module.app.state,'store',store)
    monkeypatch.setattr(module.orchestrator,'store',store)
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(module.bundles,'root',lambda:tmp_path)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
    app=TenantMiddleware(module.app,verifier=Verifier())
    # Don't start local legacy worker lifespan. Cloud mode must have no shared
    # startup data scans or automatic resume until scoped engine support lands.
    with TestClient(app) as client:yield client,store,tmp_path


def header(user):return {'Authorization':'Bearer '+user}


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
    assert client.delete('/api/tasks/'+task,headers=header('b')).status_code==404
    assert client.get('/api/tasks').status_code==401
    assert client.get('/health').json()=={'ok':True}


def test_session_registry_and_file_namespace(cloud):
    client,store,root=cloud
    sid=str(uuid.uuid4())
    with tenant_context(A):
        task=store.create_task(sid)
        store.set_engine_session(task['task_id'],sid)
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
    assert client.get('/api/sessions/'+sid,headers=header('a')).status_code==403


def test_unsafe_settings_local_auth_and_worker_routes_denied(cloud):
    client,_,_=cloud
    for path in ['/api/settings/artifacts-root','/api/settings/engine-python']:
        assert client.post(path,json={'path':'/tmp'},headers=header('a')).status_code==403
    for path in ['/auth/signup','/auth/signin','/auth/refresh']:
        assert client.post(path,headers=header('a')).status_code==404
    for path in ['/api/tasks/guess/start','/api/tasks/guess/model-route','/api/simulations']:
        assert client.post(path,json={},headers=header('a')).status_code==503


def test_cloud_cookie_csrf_and_revoked_session(cloud):
    import psycopg
    client,store,root=cloud
    client.cookies.set('requi_access','a')
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
    assert preview.status_code==200,preview.text
    assert preview.json()['status']=='preview'
    assert client.post(url,json={'from_phase':'needs','confirm':False},headers=header('b')).status_code==404
    assert client.post(url,json={'from_phase':'needs','confirm':True},headers=header('a')).status_code==503
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
        assert reply.status_code==200,reply.text
        assert reply.json()['usage']==[] and reply.json()['raw']==[]
        assert all(key not in reply.text for key in sensitive)
    assert client.post('/api/tasks/'+tid+'/debug.zip',json={},headers=header('a')).status_code==503
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
    assert set(client.get('/api/tasks/'+tid+'/audit',headers=header('a')).json()['totals'])=={'price','price_usd'}
    with tenant_context(A):
        assert store.events(tid)[-1]['payload']==payload
        assert store.audit_entries(tid)[0]['detail']==payload


def test_cloud_packages_fail_closed_until_customer_safe_export(cloud):
    client,store,root=cloud
    with tenant_context(A):
        task=store.create_task('private-export-'+uuid.uuid4().hex);tid=task['task_id']
    for owner,status in [('a',503),('b',404)]:
        assert client.post('/api/tasks/'+tid+'/packages',headers=header(owner)).status_code==status
        assert client.get('/api/tasks/'+tid+'/packages/'+'a'*64+'?generation=1',headers=header(owner)).status_code==status


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
    assert client.delete('/api/tasks/'+tid,headers=header('a')).status_code==200
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
    assert client.delete('/api/tasks/'+tid,headers=header('a')).status_code==200


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
        store.delete_task(tid)


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
    for path in ('/api/operator/sessions','/api/operator/credits','/api/operator/expense-totals'):
        assert client.get(path,headers=header('a')).status_code==503
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
    monkeypatch.setattr(module.app.state,'operator_read_store',reader,raising=False)
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
    monkeypatch.setattr(module.app.state,'operator_read_store',reader,raising=False)
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
    monkeypatch.setattr(module.app.state,'operator_read_store',OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-logs',frozenset({'logs.read'}))),raising=False)
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
    monkeypatch.setattr(module.app.state,'operator_read_store',OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'route-usage',frozenset({'usage.read'}))),raising=False)
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
