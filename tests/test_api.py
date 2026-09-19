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

def test_start_task_launches_interview_runner(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from fastapi.testclient import TestClient
    import asyncio
    tid='start-engine-test'
    if store.task(tid):store.delete_task(tid)
    store.create_task('Start engine test','A calm family task app',task_id=tid)
    calls=[]
    async def fake_run(task_id,command,**kwargs):calls.append((task_id,command,kwargs));return 0
    monkeypatch.setattr('delivery_api.app.runner.run',fake_run)
    from delivery_api.engine_runtime import EngineRuntime
    async def fake_resolve(engine_dir):return EngineRuntime(__import__('pathlib').Path(__import__('sys').executable),())
    monkeypatch.setattr('delivery_api.app.resolve_engine_runtime',fake_resolve)
    monkeypatch.setenv('INTERVIEW_ENGINE_DIR',str(tmp_path));tmp_path.mkdir(exist_ok=True)
    r=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert r.status_code==200 and r.json()['status']=='started'
    import time
    for _ in range(20):
        if calls:break
        time.sleep(.01)
    assert calls and calls[0][0]==tid and calls[0][1][1:3]==['-m','interview_engine.cli'] and calls[0][1][3]=='A calm family task app'
    assert store.task(tid)['state']=='running'


def test_start_task_missing_engine_dependency_is_human_readable(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from delivery_api.engine_runtime import EngineRuntimeError
    from fastapi.testclient import TestClient
    tid='preflight-failure-test'
    if store.task(tid):store.delete_task(tid)
    store.create_task('Preflight failure','A product idea',task_id=tid)
    engine=tmp_path/'interview-engine';engine.mkdir();python=tmp_path/'python'
    async def fail(_):raise EngineRuntimeError(module='langchain_core',checked=(python,),engine_dir=engine)
    monkeypatch.setattr('delivery_api.app.resolve_engine_runtime',fail);monkeypatch.setenv('INTERVIEW_ENGINE_DIR',str(engine))
    r=TestClient(app).post(f'/api/tasks/{tid}/start')
    assert r.status_code==503 and set(r.json()['detail'])=={'error','cause','next'}
    text=' '.join(r.json()['detail'].values())
    assert 'langchain_core' in text and 'pip install -e' in text and 'Traceback' not in text
    assert store.task(tid)['state']=='not_started'
