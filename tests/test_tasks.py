import json
import os
os.environ["DELIVERY_DB_PATH"] = ":memory:"

from fastapi.testclient import TestClient
from delivery_api.app import app, store


def test_task_three_states_and_phase_progress():
    with TestClient(app) as client:
        made = client.post("/api/tasks", json={"title": "Astronomy explorer", "brief": "Kids learn through play"})
        assert made.status_code == 201
        task_id = made.json()["task_id"]
        empty = client.get(f"/api/tasks/{task_id}").json()
        assert empty["state"] == "not_started" and len(empty["phases"]) == 7 and not empty["artifacts"]

        store.set_model_route(task_id, "distillation=ollama/phi4")
        store.update_phase(task_id, "intent", "running", detail="coverage 3/11", completed=3, total=11)
        store.add_event(task_id, "progress", "coverage 3/11", phase_key="intent", payload={"completed": 3, "total": 11})
        running = client.get(f"/api/tasks/{task_id}").json()
        assert running["state"] == "running" and running["phases"][0]["total"] == 11
        assert running["events"][0]["payload"]["completed"] == 3

        for key in ("intent", "conops", "needs", "srs", "architecture", "specs", "tests"):
            store.update_phase(task_id, key, "done")
        store.add_task_artifact(task_id, "tests", "Verification report", "/artifacts/report.json")
        done = client.get(f"/api/tasks/{task_id}").json()
        assert done["state"] == "done" and done["artifacts"][0]["name"] == "Verification report"


def test_rerun_previews_then_archives_with_cause_and_audit():
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"title": "Policy seam"}).json()["task_id"]
        store.add_task_artifact(task_id, "architecture", "Architecture", "/a.json")
        preview = client.post(f"/api/tasks/{task_id}/rerun", json={
            "from_phase": "srs", "confirm": False,
        })
        assert preview.json()["status"] == "preview" and preview.json()["policy"] == "archive"
        assert not store.task_artifacts(task_id)[0]["stale"]
        applied = client.post(f"/api/tasks/{task_id}/rerun", json={
            "from_phase": "srs", "confirm": True,
        })
        assert applied.status_code == 200 and applied.json()["generation"] == 2
        artifact = store.task_artifacts(task_id)[0]
        assert artifact["stale"] and "rerun from srs" in artifact["stale_reason"]
        task = store.task(task_id)
        assert all(p["gate_basis_changed"] for p in task["phases"][3:])
        audit = client.get(f"/api/tasks/{task_id}/audit").json()
        assert audit["actor_mode"] == "local user"
        assert any(e["action"] == "task.rerun_prepared" for e in audit["entries"])


def test_archive_policy_marks_downstream_stale_and_keeps_upstream():
    task_id = store.create_task("Archive")['task_id']
    store.add_task_artifact(task_id, "intent", "Intent", "/intent.json")
    store.add_task_artifact(task_id, "architecture", "Architecture", "/arch.json")
    generation = store.prepare_rerun(task_id, "srs", "archive")
    arts = {a["phase_key"]: a for a in store.task_artifacts(task_id)}
    assert generation == 2 and not arts["intent"]["stale"] and arts["architecture"]["stale"]


def test_static_ui_and_deep_link_are_served():
    with TestClient(app) as client:
        assert "VECTOR" in client.get("/").text
        assert "task-grid" in client.get("/assets/style.css").text
        assert client.get("/tasks/any/view").status_code == 200
        icon = client.get("/favicon.ico")
        assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/x-icon")
        assert b"<link rel=\"icon\" href=\"/favicon.ico\"" in client.get("/").content


def test_process_runner_streams_cli_output_and_known_count(tmp_path):
    import asyncio
    import sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore

    isolated = SQLiteStore(str(tmp_path / "runner.db"))
    task_id = isolated.create_task("Runner")['task_id']
    runner = ProcessRunner(isolated)

    async def go():
        return await runner.run(task_id, [sys.executable, "-c", "print('requirements 4/9')"], cwd=tmp_path, phase="srs")

    assert asyncio.run(go()) == 0
    task = isolated.task(task_id)
    srs = next(p for p in task["phases"] if p["phase_key"] == "srs")
    assert srs["state"] == "done"
    event = next(e for e in isolated.events(task_id) if e["kind"] == "progress")
    assert event["payload"] == {"completed": 4, "total": 9}


def test_storage_factory_is_the_only_backend_selection_point(tmp_path):
    from delivery_api.store import SQLiteStore, Storage, create_store

    local = create_store(f"sqlite:///{tmp_path / 'portable.db'}")
    assert isinstance(local, SQLiteStore)
    assert isinstance(local, Storage)
    task_id = local.create_task("Persist me")["task_id"]
    del local
    reopened = create_store(f"sqlite:///{tmp_path / 'portable.db'}")
    assert reopened.task(task_id)["title"] == "Persist me"


def test_http_layer_contains_no_database_calls():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "delivery_api" / "app.py").read_text()
    assert ".db." not in source
    assert ".execute(" not in source
    assert "sqlite3" not in source

def test_simulation_import_pause_decide_and_complete_without_process(tmp_path):
    import asyncio, json
    from delivery_api.simulation import Simulation
    from delivery_api.store import SQLiteStore
    source=tmp_path/'recorded'; source.mkdir()
    rows=[
      {'session_id':'s-1','stage':'intent','provider':'ollama','model':'phi4','prompt_tokens':3,'completion_tokens':4,'ts':'2026-09-12T10:00:00Z'},
      {'session_id':'s-1','stage':'conops','provider':'ollama','model':'phi4','prompt_tokens':5,'completion_tokens':6,'ts':'2026-09-12T10:00:02Z'},
      {'session_id':'s-1','stage':'tests','provider':'ollama','model':'phi4','prompt_tokens':7,'completion_tokens':8,'ts':'2026-09-12T10:00:04Z'},
    ]
    (source/'usage.jsonl').write_text('\n'.join(json.dumps(x) for x in rows))
    (source/'tests.json').write_text('[]')
    isolated=SQLiteStore(str(tmp_path/'sim.db')); sim=Simulation(isolated)
    task=sim.import_run(source)
    assert task['simulated'] and task['state']=='not_started' and len(task['gates'])==6
    isolated.set_gate_open(task['task_id'],'gate-conops',True)
    async def go():
      job=asyncio.create_task(sim.replay(task['task_id'],None))
      for _ in range(100):
        if isolated.task(task['task_id'])['state']=='waiting_gate': break
        await asyncio.sleep(.01)
      waiting=isolated.task(task['task_id'])
      assert waiting['active_phase']=='intent'
      isolated.decide_gate(task['task_id'],'gate-intent','redo',steer='Use a measurable outcome')
      for _ in range(100):
        if isolated.task(task['task_id'])['state']=='waiting_gate':
          isolated.decide_gate(task['task_id'],'gate-tests' if waiting['active_phase']=='tests' else 'gate-intent','approve',selected_option='Approve')
        await asyncio.sleep(.01)
        if job.done(): break
      # The conops gate was open, so no second pause before the tests event.
      await asyncio.wait_for(job,1)
    asyncio.run(go())
    final=isolated.task(task['task_id'])
    assert final['state']=='done' and final['artifact_count']==1
    audit=isolated.audit_entries(task['task_id'])
    assert any(x['action']=='gate.redo' and x['detail']['steer']=='Use a measurable outcome' for x in audit)
    assert any(e['kind']=='complete' and e['payload'].get('simulated') is None for e in isolated.events(task['task_id']))


def test_gate_decisions_require_explicit_provenance(tmp_path):
    import pytest
    from delivery_api.store import SQLiteStore
    isolated=SQLiteStore(str(tmp_path/'gates.db')); tid=isolated.create_task('Gates')['task_id']
    with pytest.raises(ValueError,match='steer'): isolated.decide_gate(tid,'gate-intent','redo')
    with pytest.raises(ValueError,match='option'): isolated.decide_gate(tid,'gate-intent','approve')
    isolated.decide_gate(tid,'gate-intent','edit',edited_text='Approved with this bounded scope')
    row=next(g for g in isolated.task(tid)['gates'] if g['gate_key']=='gate-intent')
    assert row['edited_text']=='Approved with this bounded scope'


def test_task_names_unique_rename_and_delete_cascade(tmp_path):
    import pytest
    from delivery_api.store import SQLiteStore
    s=SQLiteStore(str(tmp_path/'manage.db'))
    first=s.create_task('Alpha','one');second=s.create_task('Beta','two')
    with pytest.raises(ValueError,match='already exists'):s.create_task('alpha')
    with pytest.raises(ValueError,match='already exists'):s.rename_task(second['task_id'],'ALPHA')
    renamed=s.rename_task(second['task_id'],'Gamma')
    assert renamed['title']=='Gamma'
    s.add_event(second['task_id'],'progress','x');s.add_task_artifact(second['task_id'],'tests','x','/x')
    s.delete_task(second['task_id'])
    assert s.task(second['task_id']) is None
    assert not s.events(second['task_id']) and not s.task_artifacts(second['task_id'])
    assert any(x['action']=='task.deleted' and x['task_id']==second['task_id'] for x in s.audit_entries())


def test_task_management_api_structured_collisions_and_delete():
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        a=c.post('/api/tasks',json={'title':'Unique API Task'});assert a.status_code==201
        b=c.post('/api/tasks',json={'title':'unique api task'});assert b.status_code==409
        assert set(b.json()['detail'])=={'error','cause','next'}
        tid=a.json()['task_id']
        r=c.patch(f'/api/tasks/{tid}',json={'title':'Renamed API Task'});assert r.status_code==200 and r.json()['title']=='Renamed API Task'
        d=c.delete(f'/api/tasks/{tid}');assert d.status_code==204
        assert c.get(f'/api/tasks/{tid}').status_code==404
