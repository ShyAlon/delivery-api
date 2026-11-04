import os

import pytest
from fastapi.testclient import TestClient

from delivery_api.app import app
from delivery_api import config


@pytest.fixture()
def client(pg_scoped_workspace,monkeypatch,pg_task_app):
    signed_app=pg_task_app(pg_scoped_workspace)
    from delivery_api.app import store
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    return TestClient(signed_app,headers=pg_scoped_workspace.headers)


def _check_links_are_admin_only(monkeypatch,tmp_path,workspace):
    from tests.test_api import _legacy_pg_identity_client
    client=_legacy_pg_identity_client(monkeypatch,workspace)
    assert client.post("/links", json={"founder_label": "x"}).status_code == 403


def _check_unknown_link_rejected(monkeypatch,tmp_path,workspace):
    from tests.test_api import _legacy_pg_identity_client
    client=_legacy_pg_identity_client(monkeypatch,workspace)
    assert client.post("/runs", json={"token": "nope", "free_text": "x"}).status_code == 403


def _check_budget_hard_stop(monkeypatch,tmp_path,workspace):
    from tests.test_api import _legacy_pg_identity_client
    client=_legacy_pg_identity_client(monkeypatch,workspace)
    r = client.post("/links", json={"founder_label": "tester-2"},
                    headers={"x-admin-token": config.ADMIN_TOKEN})
    run_id = r.json()["run_id"]
    client.post("/runs", json={"token": r.json()["token"], "free_text": "a web app"})
    r = client.post(f"/runs/{run_id}/usage", json={"record_json": "{}", "cost_usd": 7.50})
    assert r.status_code == 402
    assert client.get(f"/runs/{run_id}").json()["state"] == "budget_stopped"


# ---- error handling ----

def test_budget_stop_message_is_actionable():
    from delivery_api.budget import BudgetExceeded

    e = BudgetExceeded("run-1", 6.01, 6.00)
    msg = str(e)
    assert "budget guard" in msg and "mint a new link" in msg and "no further llm calls" in msg.lower()


def test_job_failure_records_human_artifact(tmp_path,pg_scoped_workspace):
    import asyncio
    import time

    from delivery_api.errors import UserFacingError
    from delivery_api.jobs import JobQueue

    store = pg_scoped_workspace.store
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    run_id = store.create_link("t")["run_id"]
    q = JobQueue(store)

    async def boom(rid: str) -> str:
        raise UserFacingError("Ollama is not running.", "ollama serve not started", "run `ollama serve`")

    async def go():
        await q.start()
        q.register("generation", boom)
        rid = run_id
        await q.submit("generation", rid)
        for _ in range(50):
            arts = store.artifacts(rid)
            if arts:
                break
            await asyncio.sleep(0.05)
        await q.stop()
        return rid

    rid = asyncio.new_event_loop().run_until_complete(go())
    arts = store.artifacts(rid)
    assert any(a["kind"] == "generation:error" and "ollama serve" in a["ref"] for a in arts), arts

def test_completed_sessions_are_discovered_and_bundle_is_projected(client,tmp_path,monkeypatch,pg_scoped_workspace):
 import json
 from delivery_api import bundles
 store=pg_scoped_workspace.store
 tid=store.create_task('Family tasks')['task_id'];store.set_engine_session(tid,'s1')
 documents={('intent','intent_model.json'):{'problem_statement':'Family tasks'},
  ('needs','needs.json'):{'needs':[{'need_id':'N-1','statement':'Do work'}]},
  ('srs','srs.json'):{'functional':[{'requirement_id':'R-1','title':'Work','need_ids':['N-1'],'acceptance':[{'criterion_id':'AC-1','statement':'done'}]}]}}
 store.publish_task_revision_bytes(tid,[{'phase_key':phase,'name':name,'data':json.dumps(body).encode()} for (phase,name),body in documents.items()],writer_instance='test-only')
 from test_http_cutover import _PGUsage
 _PGUsage(store,tid,'spec/hands-free-run.json').write_text(json.dumps({'completed_at':'2026-09-19T00:00:00Z'}))
 _PGUsage(store,tid,'spec/bundle-integrity.json').write_text(json.dumps({'files_checked':14,'diagnostics':[]}))
 assert client.get('/api/sessions').json()['sessions'][0]['session_id']=='s1'
 data=client.get('/api/sessions/s1').json();assert len(data['graph']['nodes'])==3
 assert data['graph']['edges']==[{"from":"N-1","to":"R-1","kind":"realizes"},{"from":"R-1","to":"AC-1","kind":"accepts"}]


def test_start_task_requires_typed_http_engine_not_subprocess(monkeypatch,tmp_path,pg_scoped_workspace):
    from delivery_api.app import app
    store=pg_scoped_workspace.store
    from fastapi.testclient import TestClient
    tid='start-engine-test'
    store.create_task('Start engine test','A calm family task app',task_id=tid)
    calls=[]
    async def fake_run(*args,**kwargs):calls.append((args,kwargs));return 0
    monkeypatch.setattr('delivery_api.app.runner.run',fake_run)
    response=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert response.status_code==503 and not calls
    assert store.task(tid)['state']=='not_started'


def _check_start_task_unreachable_http_engine_is_human_readable(monkeypatch,tmp_path,pg_scoped_workspace):
    from delivery_api.app import app
    store=pg_scoped_workspace.store
    from fastapi.testclient import TestClient
    tid='preflight-failure-test'
    store.create_task('Preflight failure','A product idea',task_id=tid)
    r=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert r.status_code==503 and set(r.json()['detail'])=={'error','cause','next'}
    text=' '.join(r.json()['detail'].values())
    assert 'interview engine is not reachable' in text and 'Traceback' not in text
    assert store.task(tid)['state']=='not_started'



def test_model_route_preflight_unreachable_engine_does_not_leak_key(monkeypatch,pg_scoped_workspace,pg_task_app):
    signed_app=pg_task_app(pg_scoped_workspace)
    from delivery_api.app import app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('DEEPSEEK_API_KEY','super-secret')
    body=TestClient(signed_app,headers=pg_scoped_workspace.headers).get('/api/model-routes').json()
    assert next(r for r in body['routes'] if r['id']=='ollama')['available'] is True
    assert next(r for r in body['routes'] if r['id']=='deepseek')['available'] is False
    assert 'super-secret' not in str(body)


def test_start_task_deepseek_requires_engine_preflight(monkeypatch,tmp_path,pg_scoped_workspace):
    from delivery_api.app import app
    store=pg_scoped_workspace.store
    from fastapi.testclient import TestClient
    tid='deepseek-start-test'
    store.create_task('DeepSeek start','An idea',task_id=tid)
    monkeypatch.setenv('DEEPSEEK_API_KEY','secret')
    response=TestClient(app).post(f'/api/tasks/{tid}/start',json={'model_route':'deepseek'})
    assert response.status_code==503 and store.task(tid)['state']=='not_started'
    assert 'secret' not in str(response.json())


def test_task_audit_accumulates_tokens_and_price(pg_scoped_workspace,monkeypatch,pg_task_app):
    client=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers)
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)  # retain local detailed audit contract
    store=pg_scoped_workspace.store
    task=store.create_task('Priced audit totals');tid=task['task_id']
    store.add_event(tid,'price','Price 25 units',phase_key='intent',payload={'tokens':10,'price':25,'rates':{'scale':'credits'}})
    store.add_event(tid,'price','Price 50 units',phase_key='needs',payload={'tokens':20,'price':50,'price_usd':50.0,'rates':{'scale':'credits'}})
    body=client.get(f'/api/tasks/{tid}/audit').json()
    assert body['totals']=={'price':75.0}
    from delivery_api.app import _task_audit_totals
    totals=_task_audit_totals(tid)
    assert {k:totals[k] for k in ('tokens','price','phases_priced')}=={'tokens':30,'price':75.0,'phases_priced':2}
    assert totals['interview_calls']==0 and totals['price_usd']==75.0

def test_task_audit_excludes_legacy_scale_price_but_counts_tokens(pg_scoped_workspace,monkeypatch,pg_task_app):
    client=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers)
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)  # retain local detailed audit contract
    store=pg_scoped_workspace.store
    tid=store.create_task('Legacy scale')['task_id']
    store.add_event(tid,'price','Price 1,000,000 units',phase_key='intent',payload={'tokens':500000,'price':1000000,'rates':{'price_per_token':2.0}})
    store.add_event(tid,'price','Price 2.50 credits',phase_key='needs',payload={'tokens':200000,'price':2.5,'rates':{'scale':'credits'}})
    assert client.get(f'/api/tasks/{tid}/audit').json()['totals']=={'price':2.5}
    from delivery_api.app import _task_audit_totals
    t=_task_audit_totals(tid)
    assert t['tokens']==700000 and t['price']==2.5 and t['legacy_priced_phases']==1

def test_task_audit_uses_latest_cumulative_price_per_phase(pg_scoped_workspace,monkeypatch,pg_task_app):
    client=TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers)
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)  # retain local detailed audit contract
    store=pg_scoped_workspace.store
    tid=store.create_task('Incremental price totals')['task_id']
    store.add_event(tid,'price','Price 10',phase_key='intent',payload={'tokens':5,'price':10,'incremental':True,'rates':{'scale':'credits'}})
    store.add_event(tid,'price','Price 30',phase_key='intent',payload={'tokens':15,'price':30,'incremental':True,'rates':{'scale':'credits'}})
    body=client.get(f'/api/tasks/{tid}/audit').json()
    assert body['totals']=={'price':30.0}
    from delivery_api.app import _task_audit_totals
    totals=_task_audit_totals(tid)
    assert {k:totals[k] for k in ('tokens','price','phases_priced')}=={'tokens':15,'price':30.0,'phases_priced':1}
    assert totals['interview_calls']==0


def test_sessions_title_and_interview_status_from_retained_evidence(tmp_path,monkeypatch,pg_scoped_workspace):
 import json
 from delivery_api import bundles
 from test_http_cutover import _PGUsage
 store=pg_scoped_workspace.store
 tid=store.create_task('Fallback title')['task_id'];store.set_engine_session(tid,'live')
 usage=_PGUsage(store,tid,'interview/usage.jsonl');usage.write_text('{}\n')
 http=_PGUsage(store,tid,'interview/http-session.json')
 http.write_text(json.dumps({'status':30,'request':{'idea':'  A founder brief  '}}))
 monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'missing'))
 row=bundles.postgres_bundle(store,'live')['session']
 assert row['title']=='A founder brief' and row['phase_status']['interview']=='in_progress'
 http.write_text(json.dumps({'status':40,'request':{'idea':''}}))
 row=bundles.postgres_bundle(store,'live')['session']
 assert row['title']=='Fallback title' and row['phase_status']['interview']=='warning'
 store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':b'{"problem_statement":"Approved intent"}'}],writer_instance='test-only')
 row=bundles.postgres_bundle(store,'live')['session']
 assert row['title']=='Approved intent' and row['phase_status']['interview']=='done'


def test_sessions_spec_completion_and_http_error(pg_scoped_workspace):
 import json
 from delivery_api import bundles
 from test_http_cutover import _PGUsage
 store=pg_scoped_workspace.store
 tid=store.create_task('Test')['task_id'];store.set_engine_session(tid,'completion')
 for name,value in {'interview/http-session.json':{'status':50},'spec/bundle-integrity.json':{'files_checked':14,'diagnostics':[]},'spec/hands-free-run.json':{'completed_at':'2026-09-19T00:00:00Z'}}.items():
  _PGUsage(store,tid,name).write_text(json.dumps(value))
 row=bundles.postgres_bundle(store,'completion')['session']
 assert row['phase_status']['interview']=='error' and row['phase_status']['oracle']=='done'
 assert row['oracle_passed'] and row['files_checked']==14 and row['completed_at']=='2026-09-19T00:00:00Z'


def test_start_task_unreachable_http_engine_is_human_readable(monkeypatch,tmp_path,pg_scoped_workspace,pg_task_app):
    signed=pg_task_app(pg_scoped_workspace)
    from delivery_api import app as module
    from delivery_api.engine_client import EngineUnreachableError
    def unreachable():raise EngineUnreachableError('http://127.0.0.1:1','fake unavailable')
    monkeypatch.setattr(module.engine_client,'default_route',unreachable)
    monkeypatch.setattr('delivery_api.tenant_http._interview_route_open',lambda path:True)
    tid=pg_scoped_workspace.store.create_task('Preflight failure','A product idea')['task_id']
    response=TestClient(signed,headers=pg_scoped_workspace.headers).post(f'/api/tasks/{tid}/start')
    assert response.status_code==503
    assert set(response.json()['detail'])=={'error','cause','next'}
    assert 'interview engine is not reachable' in response.json()['detail']['error'].lower()
    assert pg_scoped_workspace.store.task(tid)['state']=='not_started'


def _legacy_pg_identity_client(monkeypatch,workspace):
    """Local legacy routes with explicit test identity, never a cloud auth proof."""
    from delivery_api import auth
    from delivery_api.tenancy import current_scope
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.app import store
    assert isinstance(store,PostgresStore)
    assert current_scope().owner_id==workspace.scope.owner_id
    actor=auth.Actor(workspace.scope.owner_id,'Legacy PG test owner',admin=True)
    monkeypatch.setattr(auth,'actor',lambda request,required=False:actor)
    monkeypatch.setattr(auth,'enabled',lambda:False)  # no retired local quota/account provisioning
    return TestClient(app)


def test_budget_hard_stop_retains_accounting_without_reopening_legacy_routes(pg_scoped_workspace,monkeypatch):
    from delivery_api import app as module
    from delivery_api.auth import Actor
    from fastapi import HTTPException
    store=pg_scoped_workspace.store;run=store.create_link('Budget test');rid=run['run_id']
    monkeypatch.setattr(module.auth,'enabled',lambda:False)
    with pytest.raises(HTTPException) as stopped:
        from starlette.requests import Request
        request=Request({'type':'http','headers':[]})
        module.add_usage(rid,module.UsageRecord(record_json='{}',cost_usd=7.5),request)
    assert stopped.value.status_code==402
    assert store.run(rid)['state']=='budget_stopped'
    assert store.total_spend()==7.5


def test_unknown_link_rejected(client):
    assert client.post('/runs',json={'token':'nope','free_text':'x'}).status_code==503


def test_legacy_links_are_unavailable_even_with_signed_identity(client):
    assert client.post('/links',json={'founder_label':'x'}).status_code==503
