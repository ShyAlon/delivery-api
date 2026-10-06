import os
os.environ["DELIVERY_DB_PATH"] = ":memory:"

import pytest
from fastapi.testclient import TestClient

from delivery_api.app import app
from delivery_api import config


@pytest.fixture()
def client():
    return TestClient(app)


def test_link_lifecycle_and_one_active_run(client):
    r = client.post("/links", json={"founder_label": "tester-1"},
                    headers={"x-admin-token": config.ADMIN_TOKEN})
    assert r.status_code == 200
    token = r.json()["token"]
    r = client.post("/runs", json={"token": token, "free_text": "a web app"})
    assert r.status_code == 200 and r.json()["state"] == "in_interview"
    # one active run per link:
    r = client.post("/runs", json={"token": token, "free_text": "again"})
    assert r.status_code == 409


def test_links_are_admin_only(client):
    assert client.post("/links", json={"founder_label": "x"}).status_code == 403


def test_unknown_link_rejected(client):
    assert client.post("/runs", json={"token": "nope", "free_text": "x"}).status_code == 403


def test_budget_hard_stop(client):
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
    assert "budget guard" in msg and "DELIVERY_PER_RUN_BUDGET_USD" in msg and "no further llm calls" in msg.lower()


def test_job_failure_records_human_artifact(tmp_path):
    import asyncio
    import time

    from delivery_api.errors import UserFacingError
    from delivery_api.jobs import JobQueue
    from delivery_api.store import SQLiteStore

    store = SQLiteStore(str(tmp_path / "t.db"))
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

def test_completed_sessions_are_discovered_and_bundle_is_projected(client,tmp_path,monkeypatch):
 import json
 from delivery_api import bundles
 root=tmp_path/"artifacts";base=root/"s1";(base/"interview").mkdir(parents=True);(base/"spec").mkdir()
 (base/"interview/intent_model.json").write_text(json.dumps({"problem_statement":"Family tasks"}))
 (base/"spec/hands-free-run.json").write_text(json.dumps({"completed_at":"2026-09-19T00:00:00Z"}))
 (base/"spec/bundle-integrity.json").write_text(json.dumps({"files_checked":14,"diagnostics":[]}))
 (base/"spec/needs.json").write_text(json.dumps({"needs":[{"need_id":"N-1","statement":"Do work"}]}))
 (base/"spec/srs.json").write_text(json.dumps({"functional":[{"requirement_id":"R-1","title":"Work","need_ids":["N-1"],"acceptance":[{"criterion_id":"AC-1","statement":"done"}]}]}))
 monkeypatch.setattr(bundles,"root",lambda:root)
 assert client.get('/api/sessions').json()['sessions'][0]['session_id']=='s1'
 data=client.get('/api/sessions/s1').json();assert len(data['graph']['nodes'])==3
 assert data['graph']['edges']==[{"from":"N-1","to":"R-1","kind":"realizes"},{"from":"R-1","to":"AC-1","kind":"accepts"}]

def test_artifacts_root_accepts_interview_only_spec_only_and_combined(client,tmp_path,monkeypatch):
 from delivery_api import bundles
 root=tmp_path/"artifacts"
 for session,area,name in (("i","interview","intent_model.json"),("s","spec","srs.json"),("both","interview","conops.json"),("both","spec","bundle-integrity.json")):
  p=root/session/area;p.mkdir(parents=True,exist_ok=True);(p/name).write_text('{}')
 monkeypatch.setattr(bundles,"SETTINGS_FILE",tmp_path/"setting")
 r=client.post('/api/settings/artifacts-root',json={'path':str(root)})
 assert r.status_code==200 and r.json()['count']==3
 rows={x['session_id']:x['phases'] for x in r.json()['sessions']}
 assert rows=={'i':['interview'],'s':['spec'],'both':['interview','spec']}
 r=client.post('/api/settings/artifacts-root',json={'path':str(tmp_path/'missing')})
 assert r.status_code==400 and set(r.json()['detail'])=={'error','cause','next'}

def test_start_task_requires_typed_http_engine_not_subprocess(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from fastapi.testclient import TestClient
    tid='start-engine-test'
    if store.task(tid):store.delete_task(tid)
    store.create_task('Start engine test','A calm family task app',task_id=tid)
    calls=[]
    async def fake_run(*args,**kwargs):calls.append((args,kwargs));return 0
    monkeypatch.setattr('delivery_api.app.runner.run',fake_run)
    response=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert response.status_code==503 and not calls
    assert store.task(tid)['state']=='not_started'


def test_start_task_unreachable_http_engine_is_human_readable(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from fastapi.testclient import TestClient
    tid='preflight-failure-test'
    if store.task(tid):store.delete_task(tid)
    store.create_task('Preflight failure','A product idea',task_id=tid)
    r=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert r.status_code==503 and set(r.json()['detail'])=={'error','cause','next'}
    text=' '.join(r.json()['detail'].values())
    assert 'interview engine is not reachable' in text and 'Traceback' not in text
    assert store.task(tid)['state']=='not_started'


def test_engine_python_settings_show_candidates_and_persist(monkeypatch,tmp_path,client):
    from delivery_api import engine_runtime
    good=tmp_path/'python';good.write_text('#!/bin/sh\nexit 0\n');good.chmod(0o755)
    monkeypatch.setattr(engine_runtime,'SETTINGS_FILE',tmp_path/'.engine-python')
    monkeypatch.setattr('delivery_api.app.discover_engine_runtimes',lambda _dir: __import__('asyncio').sleep(0,result=[good]))
    async def save(path,_dir):engine_runtime.SETTINGS_FILE.write_text(path+'\n');return good
    monkeypatch.setattr('delivery_api.app.save_python',save)
    result=client.get('/api/settings/engine-python').json()
    assert result['count']==1 and result['candidates']==[str(good)]
    response=client.post('/api/settings/engine-python',json={'path':str(good)})
    assert response.status_code==200 and response.json()['path']==str(good)

def test_model_route_preflight_unreachable_engine_does_not_leak_key(monkeypatch):
    from delivery_api.app import app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('DEEPSEEK_API_KEY','super-secret')
    body=TestClient(app).get('/api/model-routes').json()
    assert all(route['available'] is False for route in body['routes'])
    assert 'super-secret' not in str(body)


def test_start_task_deepseek_requires_engine_preflight(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from fastapi.testclient import TestClient
    tid='deepseek-start-test'
    if store.task(tid):store.delete_task(tid)
    store.create_task('DeepSeek start','An idea',task_id=tid)
    monkeypatch.setenv('DEEPSEEK_API_KEY','secret')
    response=TestClient(app).post(f'/api/tasks/{tid}/start',json={'model_route':'deepseek'})
    assert response.status_code==503 and store.task(tid)['state']=='not_started'
    assert 'secret' not in str(response.json())


def test_task_audit_accumulates_tokens_and_price(client):
    from delivery_api.app import store
    task=store.create_task('Priced audit totals');tid=task['task_id']
    store.add_event(tid,'price','Price 25 units',phase_key='intent',payload={'tokens':10,'price':25,'rates':{'scale':'credits'}})
    store.add_event(tid,'price','Price 50 units',phase_key='needs',payload={'tokens':20,'price':50,'price_usd':50.0,'rates':{'scale':'credits'}})
    body=client.get(f'/api/tasks/{tid}/audit').json()
    assert {k:body['totals'][k] for k in ('tokens','price','phases_priced')}=={'tokens':30,'price':75.0,'phases_priced':2}
    assert body['totals']['interview_calls']==0 and body['totals']['price_usd']==75.0

def test_task_audit_excludes_legacy_scale_price_but_counts_tokens(client):
    from delivery_api.app import store
    tid=store.create_task('Legacy scale')['task_id']
    store.add_event(tid,'price','Price 1,000,000 units',phase_key='intent',payload={'tokens':500000,'price':1000000,'rates':{'price_per_token':2.0}})
    store.add_event(tid,'price','Price 2.50 credits',phase_key='needs',payload={'tokens':200000,'price':2.5,'rates':{'scale':'credits'}})
    t=client.get(f'/api/tasks/{tid}/audit').json()['totals']
    assert t['tokens']==700000 and t['price']==2.5 and t['legacy_priced_phases']==1

def test_task_audit_uses_latest_cumulative_price_per_phase(client):
    from delivery_api.app import store
    tid=store.create_task('Incremental price totals')['task_id']
    store.add_event(tid,'price','Price 10',phase_key='intent',payload={'tokens':5,'price':10,'incremental':True,'rates':{'scale':'credits'}})
    store.add_event(tid,'price','Price 30',phase_key='intent',payload={'tokens':15,'price':30,'incremental':True,'rates':{'scale':'credits'}})
    body=client.get(f'/api/tasks/{tid}/audit').json()
    assert {k:body['totals'][k] for k in ('tokens','price','phases_priced')}=={'tokens':15,'price':30.0,'phases_priced':1}
    assert body['totals']['interview_calls']==0


def test_sessions_sort_title_and_interview_status_from_live_artifacts(tmp_path):
 import json,os
 from datetime import datetime,timezone
 from delivery_api import bundles
 root=tmp_path/'artifacts';root.mkdir()
 old=root/'old';(old/'interview').mkdir(parents=True)
 (old/'interview/intent_model.json').write_text(json.dumps({'problem_statement':'Approved intent'}))
 (old/'spec').mkdir()
 (old/'spec/hands-free-run.json').write_text(json.dumps({'completed_at':'2026-09-19T00:00:00Z'}))
 live=root/'live';(live/'interview').mkdir(parents=True)
 (live/'interview/usage.jsonl').write_text('{}\n')
 (live/'interview/http-session.json').write_text(json.dumps({'status':30,'request':{'idea':'  A founder brief  '},'open_input':{'prompt':'Question?'}}))
 stamp=datetime(2026,9,27,tzinfo=timezone.utc).timestamp()
 for path in (live/'interview/usage.jsonl',live/'interview/http-session.json'):os.utime(path,(stamp,stamp))
 rows=bundles._discover(root)
 assert [row['session_id'] for row in rows]==['live','old']
 assert rows[0]['title']=='A founder brief'
 assert rows[0]['phase_status']['interview']=='in_progress'
 assert rows[0]['updated_at'].startswith('2026-09-27T00:00:00')
 assert rows[1]['phase_status']['interview']=='done'
 assert rows[1]['title']=='Approved intent'
 assert rows[1]['completed_at']=='2026-09-19T00:00:00Z'
 # Usage alone is a trace of activity, never proof of an approved result.
 (live/'interview/http-session.json').unlink()
 assert bundles._discover(root)[0]['phase_status']['interview']=='in_progress'
 # A malformed/empty persisted request still cannot blank a card.
 (live/'interview/http-session.json').write_text(json.dumps({'status':40,'request':{'idea':''}}))
 assert bundles._discover(root)[0]['title']=='live'
 assert bundles._discover(root)[0]['phase_status']['interview']=='warning'
 # Card and bundle detail use the same interview status semantics.
 original=bundles.root
 try:
  bundles.root=lambda:root
  assert bundles.bundle('live')['phase_status']['interview']=='warning'
 finally:bundles.root=original


def test_sessions_spec_completion_and_http_error(tmp_path):
 import json
 from delivery_api import bundles
 root=tmp_path/'artifacts';session=root/'s';(session/'interview').mkdir(parents=True);(session/'spec').mkdir()
 (session/'interview/usage.jsonl').write_text('{}\n')
 (session/'interview/http-session.json').write_text(json.dumps({'status':50}))
 (session/'spec/bundle-integrity.json').write_text(json.dumps({'files_checked':14,'diagnostics':[]}))
 (session/'spec/hands-free-run.json').write_text(json.dumps({'completed_at':'2026-09-19T00:00:00Z'}))
 row=bundles._discover(root)[0]
 assert row['phase_status']['interview']=='error'
 assert row['phase_status']['oracle']=='done'
 assert row['oracle_passed'] and row['files_checked']==14
