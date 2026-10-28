import json
import os
import pytest

from fastapi.testclient import TestClient
from delivery_api.app import app, store


def test_task_three_states_and_phase_progress():
    with TestClient(app) as client:
        made = client.post("/api/tasks", json={"title": "Astronomy explorer", "brief": "Kids learn through play"})
        assert made.status_code == 201
        task_id = made.json()["task_id"]
        empty = client.get(f"/api/tasks/{task_id}").json()
        assert empty["state"] == "not_started" and len(empty["phases"]) == 8 and not empty["artifacts"]

        store.set_model_route(task_id, "distillation=ollama/phi4")
        store.update_phase(task_id, "intent", "running", detail="coverage 3/11", completed=3, total=11)
        store.add_event(task_id, "progress", "coverage 3/11", phase_key="intent", payload={"completed": 3, "total": 11})
        running = client.get(f"/api/tasks/{task_id}").json()
        assert running["state"] == "running" and running["phases"][0]["total"] == 11
        assert running["events"][0]["payload"]["completed"] == 3

        for key in ("intent", "conops", "needs", "srs", "architecture", "specs", "tests", "validation"):
            store.update_phase(task_id, key, "done")
        store.add_task_artifact(task_id, "tests", "Verification report", "/artifacts/report.json")
        done = client.get(f"/api/tasks/{task_id}").json()
        assert done["state"] == "done" and done["artifacts"][0]["name"] == "Verification report"


def test_rerun_previews_then_archives_with_cause_and_audit():
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"title": "Policy seam"}).json()["task_id"]
        store.add_task_artifact(task_id, "architecture", "Architecture", "/a.json")
        for phase in ("intent","conops","needs"): store.update_phase(task_id,phase,"done")
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
    for phase in ("intent","conops","needs"): store.update_phase(task_id,phase,"done")
    generation = store.prepare_rerun(task_id, "srs", "archive")
    arts = {a["phase_key"]: a for a in store.task_artifacts(task_id)}
    assert generation == 2 and not arts["intent"]["stale"] and arts["architecture"]["stale"]


def test_static_ui_and_deep_link_are_served():
    with TestClient(app) as client:
        html=client.get("/").text
        assert "Requi.re" in html and "VECTOR" not in html and "Vector" not in html
        assert "task-grid" in client.get("/assets/style.css").text
        assert client.get("/tasks/any/view").status_code == 200
        icon = client.get("/favicon.ico")
        assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/x-icon")
        assert b"<link rel=\"icon\" href=\"/favicon.ico\"" in client.get("/").content


def test_process_runner_streams_cli_output_and_known_count(tmp_path,pg_scoped_workspace):
    import asyncio
    import sys
    from delivery_api.runner import ProcessRunner

    isolated = pg_scoped_workspace.store
    task_id = isolated.create_task("Runner")['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(isolated,PostgresStore)
    runner = ProcessRunner(isolated)

    async def go():
        return await runner.run(task_id, [sys.executable, "-c", "print('requirements 4/9')"], cwd=tmp_path, phase="srs")

    assert asyncio.run(go()) != 0
    task = isolated.task(task_id)
    srs = next(p for p in task["phases"] if p["phase_key"] == "srs")
    assert srs["state"] == "failed"
    assert next(g for g in task["gates"] if g["after_phase"] == "srs")["state"] == "idle"
    event = next(e for e in isolated.events(task_id) if e["kind"] == "progress")
    assert event["payload"] == {"completed": 4, "total": 9}


def test_process_runner_uses_child_stage_aware_heartbeat_without_generic_noise(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;task_id=store.create_task('Useful heartbeat')['task_id']
    script='print("[17:01:25] ... still working - stage \'conops\' call, 30s elapsed",flush=True)'
    assert asyncio.run(ProcessRunner(store).run(task_id,[sys.executable,'-c',script],cwd=tmp_path))==0
    events=store.events(task_id)
    useful=next(e for e in events if 'still working' in e['message'])
    assert useful['kind']=='progress' and useful['phase_key']=='conops'
    assert "stage 'conops' call" in useful['message'] and '30s elapsed' in useful['message']
    assert not any(e['message']=='Engine is still working' for e in events)


def test_subprocess_prompt_lines_are_diagnostics_not_input_waits(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;task_id=store.create_task('Waiting prompt')['task_id']
    script="print('Generating interview question',flush=True);print('What matters?\\nYou:',end='',flush=True)"
    assert asyncio.run(ProcessRunner(store).run(task_id,[sys.executable,'-c',script],cwd=tmp_path))==0
    events=store.events(task_id)
    assert any(e['kind']=='progress' for e in events)
    # Subprocesses are non-interactive executors: prompt text on stdout is
    # diagnostic output, never an input wait (protocol retired in 8bfda8d).
    assert not any(e['kind']=='waiting_input' for e in events)

def test_answer_endpoint_appends_visible_transcript_line(monkeypatch,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.auth import Actor
    from delivery_api.app import InterviewAnswer
    task_id=app_module.store.create_task('Answer transcript')['task_id']
    async def accepted(task,text,decision_id=None):assert task==task_id and text=='Visible answer' and decision_id is None
    monkeypatch.setattr(app_module.runner,'send_input',accepted)
    from fastapi import HTTPException
    with __import__('pytest').raises(HTTPException) as rejected:
        asyncio.run(app_module.answer_interview(task_id,InterviewAnswer(answer='Visible answer'),Actor('test','Tester')))
    assert rejected.value.status_code==409
    assert not any(event['kind']=='answer' for event in app_module.store.events(task_id))


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
    import ast
    module=ast.parse(source)
    # The historical checkpoint subprocess is a string, not a DB call in the HTTP layer.
    assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='execute' for node in ast.walk(module))
    assert not any(isinstance(node,ast.Import) and any(alias.name=='sqlite3' for alias in node.names) for node in ast.walk(module))

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
    assert task['simulated'] and task['state']=='not_started' and len(task['gates'])==8
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


def test_gate_decisions_require_explicit_provenance(tmp_path,pg_scoped_workspace):
    import pytest
    isolated=pg_scoped_workspace.store; tid=isolated.create_task('Gates')['task_id']
    isolated.update_phase(tid,'intent','awaiting_approval');isolated.wait_at_gate(tid,'intent')
    with pytest.raises(ValueError,match='steer'): isolated.decide_gate(tid,'gate-intent','redo')
    with pytest.raises(ValueError,match='option'): isolated.decide_gate(tid,'gate-intent','approve')
    isolated.decide_gate(tid,'gate-intent','edit',edited_text='Approved with this bounded scope')
    row=next(g for g in isolated.task(tid)['gates'] if g['gate_key']=='gate-intent')
    assert row['edited_text']=='Approved with this bounded scope'


def test_task_names_unique_rename_and_delete_cascade(tmp_path,pg_scoped_workspace):
    import pytest
    s=pg_scoped_workspace.store
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(s,PostgresStore)
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


def test_task_management_api_structured_collisions_and_delete(pg_workspace_factory,pg_task_app):
    from fastapi.testclient import TestClient
    with pg_workspace_factory() as workspace:
        with TestClient(pg_task_app(workspace),headers=workspace.headers) as c:
            a=c.post('/api/tasks',json={'title':'Unique API Task'});assert a.status_code==201
            b=c.post('/api/tasks',json={'title':'unique api task'});assert b.status_code==409
            assert set(b.json()['detail'])=={'error','cause','next'}
            tid=a.json()['task_id']
            r=c.patch(f'/api/tasks/{tid}',json={'title':'Renamed API Task'});assert r.status_code==200 and r.json()['title']=='Renamed API Task'
            d=c.delete(f'/api/tasks/{tid}');assert d.status_code==200 and d.json()['source_artifacts_deleted'] is False
            assert c.get(f'/api/tasks/{tid}').status_code==404

def test_engine_runtime_skips_missing_dependency_and_selects_working_python(tmp_path,monkeypatch):
    import asyncio,os
    from delivery_api.engine_runtime import resolve_engine_runtime
    engine=tmp_path/'interview-engine';engine.mkdir()
    bad=tmp_path/'bad';good=engine/'.venv/bin';bad.mkdir();good.mkdir(parents=True)
    for path,rc in ((bad/'python',1),(good/'python',0)):
        path.write_text(f'#!/bin/sh\nexit {rc}\n');path.chmod(0o755)
    monkeypatch.setenv('INTERVIEW_ENGINE_PYTHON',str(bad/'python'))
    monkeypatch.setattr('delivery_api.engine_runtime.saved_python',lambda:None)
    runtime=asyncio.run(resolve_engine_runtime(engine))
    assert runtime.python==good.joinpath('python').absolute()
    assert runtime.checked==(bad.joinpath('python').absolute(),good.joinpath('python').absolute())


def test_engine_runtime_missing_dependency_reports_all_interpreters(tmp_path,monkeypatch):
    import asyncio
    from delivery_api.engine_runtime import EngineRuntimeError,resolve_engine_runtime
    engine=tmp_path/'interview-engine';python=engine/'.venv/bin/python';python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nexit 1\n');python.chmod(0o755)
    monkeypatch.setenv('INTERVIEW_ENGINE_PYTHON',str(python))
    monkeypatch.setattr('delivery_api.engine_runtime._candidates',lambda _: [python])
    try:asyncio.run(resolve_engine_runtime(engine))
    except EngineRuntimeError as e:
        assert e.module=='langchain_core' and e.checked==(python,)
    else:assert False,'missing dependency preflight should fail'


def test_process_runner_hides_traceback_and_classifies_missing_module(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    isolated=pg_scoped_workspace.store;task_id=isolated.create_task('Failure')['task_id']
    script="import sys; print('Traceback (most recent call last):'); print('  File \\\"x.py\\\", line 1'); print(\\\"ModuleNotFoundError: No module named 'langchain_core'\\\"); sys.exit(1)"
    assert asyncio.run(ProcessRunner(isolated).run(task_id,[sys.executable,'-c',script],cwd=tmp_path))==1
    events=isolated.events(task_id);visible=' '.join(e['message'] for e in events)
    assert 'Traceback' not in visible and 'File "x.py"' not in visible
    error=next(e for e in events if e['kind']=='error')
    assert 'langchain_core' in error['message'] and 'Likely cause:' in error['message'] and error['payload']['exit_code']==1


def test_rerun_refuses_stage_with_unfulfilled_prerequisite():
    with TestClient(app) as client:
        task_id=store.create_task('Sequential rerun guard')['task_id']
        store.update_phase(task_id,'intent','done')
        response=client.post(f'/api/tasks/{task_id}/rerun',json={'from_phase':'needs','confirm':False})
        assert response.status_code==409
        detail=response.json()['detail']
        assert set(detail)=={'error','cause','next'}
        assert 'Concept of operations' in detail['error'] and "rerun 'Concept of operations' first" in detail['next']
        assert store.task(task_id)['generation']==1


def test_rerun_proceeds_when_every_upstream_phase_is_done():
    with TestClient(app) as client:
        task_id=store.create_task('Sequential rerun allowed')['task_id']
        for phase in ('intent','conops','needs'):store.update_phase(task_id,phase,'done')
        preview=client.post(f'/api/tasks/{task_id}/rerun',json={'from_phase':'srs','confirm':False})
        assert preview.status_code==200 and preview.json()['from_phase']=='srs'

def test_engine_runtime_persisted_setting_precedes_fallback(tmp_path,monkeypatch):
    from delivery_api import engine_runtime
    saved=tmp_path/'saved-python';fallback=tmp_path/'fallback-python'
    for path in (saved,fallback):path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o755)
    monkeypatch.setattr(engine_runtime,'SETTINGS_FILE',tmp_path/'.engine-python');engine_runtime.SETTINGS_FILE.write_text(str(saved))
    monkeypatch.delenv('INTERVIEW_ENGINE_PYTHON',raising=False);monkeypatch.delenv('VMODEL_PYTHON',raising=False);monkeypatch.delenv('VIRTUAL_ENV',raising=False)
    monkeypatch.setattr(engine_runtime.sys,'executable',str(fallback))
    assert engine_runtime._candidates(tmp_path/'interview-engine')[0]==saved.absolute()


def test_engine_python_setting_rejects_interpreter_without_dependency(tmp_path,monkeypatch):
    import asyncio
    from delivery_api import engine_runtime
    bad=tmp_path/'python';bad.write_text('#!/bin/sh\nexit 1\n');bad.chmod(0o755)
    monkeypatch.setattr(engine_runtime,'SETTINGS_FILE',tmp_path/'.engine-python')
    try:asyncio.run(engine_runtime.save_python(str(bad),tmp_path))
    except ValueError as e:assert 'langchain_core' in str(e)
    else:assert False,'bad engine Python must not persist'
    assert not engine_runtime.SETTINGS_FILE.exists()


def test_venv_python_symlink_is_invoked_literally_for_preflight(tmp_path,monkeypatch):
    import asyncio,subprocess,sys
    from delivery_api import engine_runtime
    venv=tmp_path/'engine-venv';subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    python=venv/'bin/python'
    probe=venv/('Lib/site-packages' if sys.platform=='win32' else f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')/'venv_only_probe.py'
    probe.parent.mkdir(parents=True,exist_ok=True);probe.write_text('VALUE=1\n')
    assert python.is_symlink(), 'regression requires the normal symlinked venv interpreter'
    assert not asyncio.run(engine_runtime.validate_python(engine_runtime.Path(sys.executable),'venv_only_probe'))
    monkeypatch.setattr(engine_runtime,'_candidates',lambda _:[python])
    runtime=asyncio.run(engine_runtime.resolve_engine_runtime(tmp_path,required_module='venv_only_probe'))
    assert runtime.python==python.absolute() and runtime.attempts==((python,True),)
    assert runtime.python!=python.resolve()

def test_built_runtime_does_not_fall_back_to_legacy_interview_subprocess(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from fastapi.testclient import TestClient
    task_id='dist-runtime-test'
    if store.task(task_id):store.delete_task(task_id)
    store.create_task('Built runtime','One execution path',task_id=task_id)
    calls=[]
    async def fake_run(*args,**kwargs):calls.append((args,kwargs));return 0
    from delivery_api import auth
    auth.limiter=auth.RateLimiter()
    monkeypatch.setenv('REQUI_RE_DIST','1')
    monkeypatch.setattr('delivery_api.app.runner.run',fake_run)
    # Starting requires the typed HTTP engine, even in a built runtime.
    response=TestClient(app).post(f'/api/tasks/{task_id}/start')
    assert response.status_code==503 and not calls


def test_default_database_is_stable_user_data_outside_repo_and_dist(monkeypatch,tmp_path):
    import importlib
    from pathlib import Path
    from delivery_api import config
    monkeypatch.delenv('DELIVERY_DATABASE_URL',raising=False);monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'xdg'))
    assert config.default_data_dir()==tmp_path/'xdg'/'requi-re'
    assert config.default_data_dir()/'delivery.db' not in (tmp_path/'dist',Path.cwd())

def test_turso_replica_writes_locally_and_syncs_account_session(monkeypatch,tmp_path):
    import sqlite3
    from delivery_api.store import TursoReplicaStore
    class Replica:
        def __init__(self,path):self.inner=sqlite3.connect(path,check_same_thread=False);self.inner.row_factory=sqlite3.Row;self.syncs=[]
        def execute(self,*a,**k):return self.inner.execute(*a,**k)
        def executemany(self,*a,**k):return self.inner.executemany(*a,**k)
        def executescript(self,*a,**k):return self.inner.executescript(*a,**k)
        def commit(self):return self.inner.commit()
        def sync(self):self.syncs.append('attempted')
    replica=Replica(tmp_path/'replica.db');events=[]
    monkeypatch.setattr('delivery_api.store.activity',lambda kind,message,**fields:events.append((kind,message,fields)))
    store=TursoReplicaStore(str(tmp_path/'replica.db'),'libsql://database.example','secret-token',connection=replica)
    user=store.create_basic_user('cloud@example.com','salt$hash',100)
    store.create_session('opaque-session',user['user_id'],9999999999)
    store.delete_session('opaque-session')
    assert store.basic_user('cloud@example.com')['user_id']==user['user_id']
    assert len(replica.syncs)==4
    rendered=repr(events)
    assert 'secret-token' not in rendered and 'libsql://database.example' not in rendered


def test_turso_sync_failure_keeps_local_write_available(monkeypatch,tmp_path):
    import sqlite3
    from delivery_api.store import TursoReplicaStore
    class OfflineReplica:
        def __init__(self,path):self.inner=sqlite3.connect(path,check_same_thread=False);self.inner.row_factory=sqlite3.Row
        def execute(self,*a,**k):return self.inner.execute(*a,**k)
        def executemany(self,*a,**k):return self.inner.executemany(*a,**k)
        def executescript(self,*a,**k):return self.inner.executescript(*a,**k)
        def commit(self):return self.inner.commit()
        def sync(self):raise OSError('network offline')
    events=[];monkeypatch.setattr('delivery_api.store.activity',lambda kind,message,**fields:events.append((kind,message,fields)))
    store=TursoReplicaStore(str(tmp_path/'offline.db'),'libsql://database.example','secret-token',connection=OfflineReplica(tmp_path/'offline.db'))
    user=store.create_basic_user('offline@example.com','salt$hash',100)
    assert store.basic_user('offline@example.com')['user_id']==user['user_id']
    assert any(kind=='database.sync.failed' for kind,_,_ in events)
    assert 'secret-token' not in repr(events)

def test_turso_initial_offline_start_falls_back_to_local_sqlite(monkeypatch,tmp_path):
    from delivery_api.store import TursoReplicaStore
    events=[]
    monkeypatch.setattr('delivery_api.libsql_store.connect_replica',lambda *a,**k:(_ for _ in ()).throw(OSError('offline token must not leak')))
    monkeypatch.setattr('delivery_api.store.activity',lambda kind,message,**fields:events.append((kind,message,fields)))
    store=TursoReplicaStore(str(tmp_path/'startup-offline.db'),'libsql://database.example','super-secret-token')
    user=store.create_basic_user('startup-offline@example.com','salt$hash',100)
    assert store.basic_user('startup-offline@example.com')['user_id']==user['user_id']
    rendered=repr(events)
    assert 'database.replica.offline' in rendered and 'database.sync.failed' in rendered
    assert 'super-secret-token' not in rendered and 'libsql://database.example' not in rendered

def test_turso_tuple_rows_obey_complete_storage_row_contract(tmp_path):
    """Real libSQL returns tuples; every storage method must still see mapping rows."""
    import libsql
    from delivery_api.store import TursoReplicaStore
    raw=libsql.connect(str(tmp_path/'tuple-replica.db'))
    class LocalReplica:
        def execute(self,*a,**k):return raw.execute(*a,**k)
        def executemany(self,*a,**k):return raw.executemany(*a,**k)
        def executescript(self,*a,**k):return raw.executescript(*a,**k)
        def commit(self):return raw.commit()
        def sync(self):pass
    store=TursoReplicaStore(str(tmp_path/'tuple-replica.db'),'libsql://unused.example','token',connection=LocalReplica())
    link=store.create_link('founder');assert store.link(link['token'])['run_id']==link['run_id']
    task_id=store.create_task('Tuple row task','Adapter contract')['task_id']
    store.update_phase(task_id,'intent','running',detail='Interviewing')
    store.add_event(task_id,'progress','Question ready',phase_key='intent',payload={'completed':1,'total':2})
    store.audit('tester','adapter.checked',task_id,{'ok':True})
    store.add_task_artifact(task_id,'intent','Intent','/intent.json')
    task=store.task(task_id)
    assert task['title']=='Tuple row task' and task['phases'][0]['detail']=='Interviewing'
    assert store.events(task_id)[-1]['payload']=={'completed':1,'total':2}
    assert any(e['detail']=={'ok':True} for e in store.audit_entries(task_id))
    assert store.task_artifacts(task_id)[0]['name']=='Intent'


def test_sse_stream_reads_events_from_turso_tuple_backend(monkeypatch,tmp_path):
    import asyncio,json,libsql
    from delivery_api import app as app_module
    from delivery_api.store import TursoReplicaStore
    raw=libsql.connect(str(tmp_path/'sse-replica.db'))
    class LocalReplica:
        def execute(self,*a,**k):return raw.execute(*a,**k)
        def executemany(self,*a,**k):return raw.executemany(*a,**k)
        def executescript(self,*a,**k):return raw.executescript(*a,**k)
        def commit(self):return raw.commit()
        def sync(self):pass
    store=TursoReplicaStore(str(tmp_path/'sse-replica.db'),'libsql://unused.example','token',connection=LocalReplica())
    task_id=store.create_task('SSE tuple task','')['task_id']
    monkeypatch.setattr(app_module,'store',store)
    async def read():
        class Connected:
            async def is_disconnected(self):return False
        response=await app_module.task_stream(task_id,Connected())
        iterator=response.body_iterator
        heartbeat=await anext(iterator)
        assert 'event: heartbeat' in heartbeat
        store.add_event(task_id,'progress','libSQL event',phase_key='intent',payload={'completed':1})
        chunk=await asyncio.wait_for(anext(iterator),3)
        assert 'event: progress' in chunk
        data=json.loads(chunk.split('data: ',1)[1])
        assert data['message']=='libSQL event' and data['payload']=={'completed':1}
    asyncio.run(read())

def test_process_runner_emits_every_question_line_as_progress(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;task_id=store.create_task('Three questions')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    questions=['First question?','Second question?','Third question?']
    script="".join(f"print({q!r},flush=True)\n" for q in questions)
    assert asyncio.run(ProcessRunner(store).run(task_id,[sys.executable,'-c',script],cwd=tmp_path))==0
    lines=[e['message'] for e in store.events(task_id) if e['kind']=='progress']
    assert [q for q in lines if q in questions]==questions
    assert not any(e['kind']=='waiting_input' for e in store.events(task_id))

def test_generated_phase_waits_yellow_until_founder_approval(tmp_path,pg_scoped_workspace):
    s=pg_scoped_workspace.store;tid=s.create_task('Approval boundary')['task_id']
    s.update_phase(tid,'conops','running',detail='Generating concept options')
    s.update_phase(tid,'conops','awaiting_approval',detail='Generated; waiting for founder approval')
    gate=s.wait_at_gate(tid,'conops');task=s.task(tid)
    assert gate['state']=='waiting' and task['state']=='waiting_gate'
    assert next(p for p in task['phases'] if p['phase_key']=='conops')['state']=='awaiting_approval'
    assert s.rerun_prerequisite(tid,'needs')['phase_key']=='intent'
    s.update_phase(tid,'intent','approved',detail='Founder approved')
    assert s.rerun_prerequisite(tid,'needs')['phase_key']=='conops'
    s.decide_gate(tid,'gate-conops','approve',selected_option='Approve Concept of operations',actor='founder@example.com')
    phase=next(p for p in s.task(tid)['phases'] if p['phase_key']=='conops')
    assert phase['state']=='approved' and phase['detail']=='Founder approved'
    audit=next(a for a in s.audit_entries(tid) if a['action']=='gate.approve')
    assert audit['actor']=='founder@example.com' and audit['detail']['phase']=='conops' and audit['detail']['approved_at']


def test_open_gate_is_explicit_audited_bypass(tmp_path,pg_scoped_workspace):
    s=pg_scoped_workspace.store;tid=s.create_task('Policy bypass')['task_id']
    s.update_phase(tid,'intent','awaiting_approval',detail='Generated')
    s.set_gate_open(tid,'gate-intent',True,actor='admin@example.com')
    assert s.wait_at_gate(tid,'intent') is None
    assert next(p for p in s.task(tid)['phases'] if p['phase_key']=='intent')['state']=='approved'
    assert any(a['action']=='gate.bypassed' and a['detail']['provenance']=='explicit open-gate policy' for a in s.audit_entries(tid))


def test_interview_preview_does_not_color_downstream_requirements(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    s=SQLiteStore(str(tmp_path/'routing.db'));tid=s.create_task('No premature requirements')['task_id']
    script="print('[17:00:00] -> Building scope and framing (phase 2/4)',flush=True);print('[17:00:01] ok Drafting requirement preview (phase 3/4)',flush=True)"
    asyncio.run(ProcessRunner(s).run(tid,[sys.executable,'-c',script],cwd=tmp_path))
    task=s.task(tid)
    assert next(p for p in task['phases'] if p['phase_key']=='srs')['state']=='pending'
    assert all(e['phase_key']=='intent' for e in s.events(tid) if e['kind']=='progress')

def test_plain_sqlite_in_turso_mode_falls_back_before_native_metadata_error(monkeypatch,tmp_path):
    import sqlite3
    from delivery_api.store import TursoReplicaStore
    db=tmp_path/'delivery.db';sqlite3.connect(db).execute('create table preserved(value text)').connection.commit()
    events=[];monkeypatch.setattr('delivery_api.store.activity',lambda kind,message,**fields:events.append((kind,message,fields)))
    store=TursoReplicaStore(str(db),'libsql://database.example','secret-token')
    assert store._replica_available is False
    assert any(k=='database.replica.offline' and 'no embedded-replica metadata' in f['error'] for k,_,f in events)
    assert sqlite3.connect(db).execute("select name from sqlite_master where name='preserved'").fetchone()


def test_startup_marks_unbound_pre_http_checkpoint_incompatible(monkeypatch,tmp_path):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'resume.db'));tid=store.create_task('Interrupted')['task_id']
    store.update_phase(tid,'intent','running',detail='Interviewing')
    store.add_event(tid,'progress','session: 20260919-163600-abcd',phase_key='intent')
    monkeypatch.setattr(app_module,'store',store)
    asyncio.run(app_module._resume_interrupted_interviews())
    assert any(e['kind']=='incompatible' for e in store.events(tid))
    assert store.engine_session(tid) is None


def test_shutdown_interruption_preserves_running_state_for_resume(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'shutdown.db'));tid=store.create_task('Restart me')['task_id'];runner=ProcessRunner(store)
    async def go():
        job=asyncio.create_task(runner.run(tid,[sys.executable,'-c','import time;print("session: checkpoint-1",flush=True);time.sleep(30)'],cwd=tmp_path))
        for _ in range(100):
            if tid in runner.processes:break
            await asyncio.sleep(.01)
        await asyncio.sleep(.05);await runner.shutdown(.2);await job
    asyncio.run(go())
    assert store.task(tid)['state']=='running'
    assert any(e['kind']=='interrupted' for e in store.events(tid))

def test_startup_does_not_replay_unbound_legacy_sigterm_checkpoint(monkeypatch,tmp_path):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'legacy-resume.db'));tid=store.create_task('Upgrade interrupted')['task_id']
    store.update_phase(tid,'intent','running');store.add_event(tid,'progress','session: legacy-checkpoint',phase_key='intent')
    store.update_phase(tid,'intent','failed');store.add_event(tid,'error','old shutdown',phase_key='intent',payload={'exit_code':-15})
    monkeypatch.setattr(app_module,'store',store)
    asyncio.run(app_module._resume_interrupted_interviews())
    assert store.engine_session(tid) is None
    assert store.task(tid)['state']=='failed'


def test_runner_preserves_structured_founder_decision_options(tmp_path):
    import asyncio
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    payload={"type":"founder_decision","decision_id":"concept","title":"Choose one","prompt":"Which concept?","options":[{"value":"1","label":"Native","summary":"Installed","advantages":["Fast"],"disadvantages":["Stores"]},{"value":"2","label":"PWA","summary":"Browser","advantages":["Deploy"],"disadvantages":["APIs"]}],"rationale_required":True,"rationale_label":"Why?","answer_format":"<value> - <rationale>"}
    store=SQLiteStore(str(tmp_path/'decision.db'));tid=store.create_task('Decision')['task_id'];runner=ProcessRunner(store)
    async def go():
        job=asyncio.create_task(runner.request_decision(tid,'intent',payload))
        await asyncio.sleep(0)
        await runner.send_input(tid,'2 - easier deployment','concept')
        return await job
    assert asyncio.run(go())=='2 - easier deployment'
    event=next(e for e in store.events(tid) if e['kind']=='founder_decision')
    assert event['payload']['decision']['options'][1]['label']=='PWA'

def test_fragmented_envelope_bytes_never_become_a_decision(tmp_path):
    import asyncio,json,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    options=[{"value":str(i),"label":f"Option {i}","summary":"Long description "*20,"advantages":["A"*90],"disadvantages":["D"*90]} for i in range(1,5)]
    payload={"type":"founder_decision","decision_id":"large","title":"Choose","prompt":"Pick one","options":options}
    encoded="REQUIRE_EVENT "+json.dumps(payload,separators=(',',':'))
    assert len(encoded)>512
    # Deliberately split inside JSON independently of stdout chunk behavior.
    script=f'import sys,time;data={encoded!r};sys.stdout.write(data[:300]);sys.stdout.flush();time.sleep(.05);sys.stdout.write(data[300:]+"\\nYou: ");sys.stdout.flush()'
    store=SQLiteStore(str(tmp_path/'fragment.db'));tid=store.create_task('Large decision')['task_id']
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path))==0
    events=store.events(tid)
    # Envelope bytes on stdout are diagnostics; they can never assemble into a question.
    assert not any(e['kind'] in ('founder_decision','waiting_input') for e in events)

def test_activity_mirrors_phase_events_to_data_stage_log(monkeypatch,tmp_path):
    from delivery_api import observability
    path=tmp_path/'logs'/'stages'/'conops.log';monkeypatch.setitem(observability.STAGE_LOG_PATHS,'conops',path)
    observability.event('engine.output','Structured stage line',task_id='t1',phase='conops')
    assert path.is_file() and 'engine.output' in path.read_text() and 'Structured stage line' in path.read_text()
    assert not (tmp_path/'logs'/'stages'/'intent.log').exists()


def test_every_phase_has_a_stable_data_log_path():
    from delivery_api.observability import STAGE_LOG_PATHS
    from delivery_api.store import PHASES
    assert list(STAGE_LOG_PATHS)==[key for key,_ in PHASES]
    assert all(str(path).endswith(f'/logs/stages/{phase}.log') for phase,path in STAGE_LOG_PATHS.items())

def test_only_active_phase_pulses_blue(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'states.db'));tid=store.create_task('Truthful states')['task_id']
    store.update_phase(tid,'intent','running',detail='Intent work')
    store.update_phase(tid,'conops','running',detail='CONOPS work')
    task=store.task(tid)
    assert next(p for p in task['phases'] if p['phase_key']=='intent')['state']=='awaiting_approval'
    assert [p['phase_key'] for p in task['phases'] if p['state']=='running']==['conops']
    script='print("Building CONOPS",flush=True)'
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path,phase='conops'))==0

def test_active_upstream_phase_resets_false_downstream_waiting_state(tmp_path):
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'downstream.db'));tid=store.create_task('Reset false downstream')['task_id']
    store.update_phase(tid,'specs','running',detail='You:')
    store.update_phase(tid,'specs','awaiting_approval',detail='You:')
    store.update_phase(tid,'intent','running',detail='Waiting for founder approval')
    task=store.task(tid);specs=next(p for p in task['phases'] if p['phase_key']=='specs')
    assert specs['state']=='pending' and specs['detail']=='Blocked on upstream approval'

def test_protocol_envelope_never_reappears_in_following_you_question(tmp_path):
    import asyncio,json,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    payload={"type":"founder_decision","decision_id":"safe","title":"Safe","prompt":"Approve the clean intent?","options":[{"value":"y","label":"Yes"},{"value":"n","label":"No"}]}
    script=f'import json;print("REQUIRE_EVENT "+json.dumps({payload!r}),flush=True);print("You: ",end="",flush=True)'
    store=SQLiteStore(str(tmp_path/'clean.db'));tid=store.create_task('Clean protocol')['task_id']
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path))==0
    # Subprocess output is diagnostics only: protocol bytes can never become a question.
    assert not any(e['kind'] in ('founder_decision','waiting_input') for e in store.events(tid))

def test_runner_can_close_engine_approved_phases(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'complete.db'));tid=store.create_task('Complete start')['task_id']
    asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c','print("done")'],cwd=tmp_path,completion_phases=('intent','conops')))
    phases={p['phase_key']:p for p in store.task(tid)['phases']}
    assert phases['intent']['state']=='approved' and phases['conops']['state']=='approved'


def test_orchestrator_phase_plan_launches_next_bounded_process(monkeypatch,tmp_path):
    generator=tmp_path/"generator";python=generator/".venv/bin/python";python.parent.mkdir(parents=True);python.touch()
    monkeypatch.setenv("SPEC_GENERATOR_DIR",str(generator))
    import asyncio,json
    from delivery_api.orchestrator import PhaseOrchestrator
    from delivery_api.phases import PHASE_DEFINITIONS
    from delivery_api.store import SQLiteStore
    assert tuple((phase.key,phase.generator_stage) for phase in PHASE_DEFINITIONS if phase.executor=="generator")==(("needs","needs"),("srs","srs"),("architecture","architecture"),("specs","items"),("tests","tests"),("validation","validation"))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
    store=SQLiteStore(str(tmp_path/'plan.db'));tid=store.create_task('Planned product')['task_id'];store.add_event(tid,'progress','session: session-1',phase_key='intent')
    interview=tmp_path/'artifacts'/'session-1'/'interview';interview.mkdir(parents=True)
    (interview/'intent_model.json').write_text(json.dumps({'contract_version':'0.16.0'}));(interview/'conops.json').write_text('{}')
    class Runner:
        calls=[]
        async def run(self,*a,**kw):self.calls.append((a,kw));return 0
    runner=Runner();orch=PhaseOrchestrator(store,runner)
    asyncio.run(orch.continue_after(tid,'conops'))
    cmd=runner.calls[0][0][1]
    assert runner.calls[0][1]['phase']=='needs' and cmd[-2:]==['--to','needs']
    assert str(interview/'intent_model.json') in cmd and str(interview/'conops.json') in cmd


def test_orchestrator_architecture_is_greenfield_without_decision(monkeypatch,tmp_path):
    generator=tmp_path/"generator";python=generator/".venv/bin/python";python.parent.mkdir(parents=True);python.touch()
    monkeypatch.setenv("SPEC_GENERATOR_DIR",str(generator))
    import asyncio,json
    from delivery_api.orchestrator import PhaseOrchestrator
    from delivery_api.store import SQLiteStore
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
    store=SQLiteStore(str(tmp_path/'baseline.db'));tid=store.create_task('New Product')['task_id'];store.add_event(tid,'progress','session: s2',phase_key='intent')
    interview=tmp_path/'artifacts'/'s2'/'interview';interview.mkdir(parents=True);(interview/'intent_model.json').write_text(json.dumps({'contract_version':'0.16.0'}));(interview/'conops.json').write_text('{}')
    class Runner:
        calls=[]
        async def request_decision(self,task_id,phase,decision):
            raise AssertionError('No baseline decision belongs in the greenfield flow')
        async def run(self,*a,**kw):self.calls.append((a,kw));return 0
    runner=Runner();asyncio.run(PhaseOrchestrator(store,runner).continue_after(tid,'srs'))
    baseline=json.loads((tmp_path/'artifacts'/'s2'/'spec'/'system-baseline.json').read_text())
    assert baseline['mode']=='greenfield' and baseline['baseline_id']!='pending-founder-choice'
    assert runner.calls[0][1]['phase']=='architecture'


def test_runner_releases_process_before_completion_callback(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Handoff')['task_id'];runner=ProcessRunner(store);observed=[]
    async def next_stage(task_id,phase):observed.append((task_id not in runner.processes,phase))
    runner.on_complete=next_stage
    asyncio.run(runner.run(tid,[sys.executable,'-c','print("session: handoff")'],cwd=tmp_path,completion_phases=('intent','conops')))
    assert observed==[(True,'conops')]


def test_open_gate_uses_same_automatic_continuation_path(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Open gate')['task_id'];store.set_gate_open(tid,'gate-needs',True)
    runner=ProcessRunner(store);observed=[]
    async def continue_after(task_id,phase):observed.append(phase)
    runner.on_complete=continue_after
    code=asyncio.run(runner.run(tid,[sys.executable,'-c','print("needs generated")'],cwd=tmp_path,phase='needs',infer_phase=False))
    assert code!=0 and observed==[]
    assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='needs')['state']=='idle'


def test_stale_answer_reports_actual_runtime_state_and_records_error(monkeypatch,pg_scoped_workspace):
    import asyncio
    from fastapi import HTTPException
    from delivery_api import app as app_module
    from delivery_api.auth import Actor
    from delivery_api.app import InterviewAnswer
    task_id=app_module.store.create_task('Stale decision truth')['task_id']
    async def stale(task,text,decision_id=None):raise RuntimeError('task is not waiting for input')
    monkeypatch.setattr(app_module.runner,'send_input',stale)
    monkeypatch.setattr(app_module.runner,'input_state',lambda task:{'waiting':False,'process_live':False,'decision':None,'requirement':None})
    try:asyncio.run(app_module.answer_interview(task_id,InterviewAnswer(answer='n'),Actor('test','Tester')))
    except HTTPException as exc:
        assert exc.status_code==409 and exc.detail['stale'] is True
        assert exc.detail['current_state']=='not_started' and exc.detail['active_phase'] is None
        assert 'Refresh the task' in exc.detail['next']
    else:raise AssertionError('stale submit should conflict')
    event=app_module.store.events(task_id)[-1]
    assert event['kind']=='error' and event['payload']['endpoint'].endswith('/answer')
    assert event['payload']['http_status']==409 and event['payload']['task_state']=='not_started'


def test_task_projection_exposes_live_input_truth(monkeypatch,pg_scoped_workspace):
    from delivery_api import app as app_module
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(app_module.store,PostgresStore)
    tid=app_module.store.create_task('Input truth projection')['task_id']
    monkeypatch.setattr(app_module.runner,'input_state',lambda task:{'waiting':False,'process_live':False,'decision':None,'requirement':None})
    result=app_module.get_task(tid)
    assert result['input_state']=={'waiting':False,'process_live':False,'decision':None,'requirement':None}


def test_restart_does_not_text_replay_gate_parked_pre_http_interview(monkeypatch,tmp_path):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'gate-parked.db'));tid=store.create_task('Resume orphaned gate')['task_id']
    store.update_phase(tid,'conops','running');store.update_phase(tid,'conops','awaiting_approval');store.wait_at_gate(tid,'conops')
    store.add_event(tid,'progress','session: orphaned-session',phase_key='conops')
    monkeypatch.setattr(app_module,'store',store)
    asyncio.run(app_module._resume_interrupted_interviews())
    assert store.task(tid)['state']=='waiting_gate'
    assert any(e['kind']=='incompatible' for e in store.events(tid))


def test_health_exposes_runtime_revision(monkeypatch):
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module,'RUNTIME_VERSION',{'runtime_id':'delivery-sha','repositories':{'delivery-api':'delivery-sha','interview-engine':'engine-sha'},'manifest':'/dist/MANIFEST.txt'})
    result=app_module.health()
    assert result['runtime_id']=='delivery-sha' and result['repositories']['interview-engine']=='engine-sha'

def test_startup_recovers_approved_interview_handoff_to_needs(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    store=pg_scoped_workspace.store;tid=store.create_task('Approved but stuck')['task_id']
    store.update_phase(tid,'intent','approved');store.update_phase(tid,'conops','approved')
    monkeypatch.setattr(app_module,'store',store);monkeypatch.setitem(app_module.app.state._state,'store',store)
    calls=[]
    async def continue_after(task_id,phase):calls.append((task_id,phase))
    monkeypatch.setattr(app_module.orchestrator,'continue_after',continue_after)
    async def go():
        await app_module._resume_orphaned_handoffs();await asyncio.sleep(.01)
    asyncio.run(go())
    assert calls==[(tid,'conops')]


def test_next_phase_launch_failure_is_visible_and_preserves_completed_boundary(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Visible handoff failure')['task_id'];runner=ProcessRunner(store)
    async def fail(task_id,phase):raise RuntimeError('missing intent_model.json')
    runner.on_complete=fail
    try:asyncio.run(runner.run(tid,[sys.executable,'-c','print("session: s")'],cwd=tmp_path,completion_phases=('intent','conops')))
    except RuntimeError:pass
    event=store.events(tid)[-1]
    assert event['kind']=='error' and 'missing intent_model.json' in event['message']
    states={p['phase_key']:p['state'] for p in store.task(tid)['phases']}
    assert states['intent']=='approved' and states['conops']=='approved'


def test_startup_handoff_recovery_is_shared_across_phase_boundaries(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    store=pg_scoped_workspace.store;tid=store.create_task('Shared recovery')['task_id']
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    monkeypatch.setattr(app_module,'store',store);monkeypatch.setitem(app_module.app.state._state,'store',store)
    calls=[]
    async def continue_after(task_id,phase):calls.append((task_id,phase))
    monkeypatch.setattr(app_module.orchestrator,'continue_after',continue_after)
    async def go():await app_module._resume_orphaned_handoffs();await asyncio.sleep(.01)
    asyncio.run(go())
    assert calls==[(tid,'needs')]


def test_typed_answer_requires_current_matching_decision(tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api.runner import ProcessRunner
    decision={'type':'founder_decision','decision_id':'live-choice','title':'Choose','prompt':'Choose?','options':[{'value':'y','label':'Yes'}]}
    store=pg_scoped_workspace.store;tid=store.create_task('Match decision')['task_id'];runner=ProcessRunner(store)
    async def go():
        job=asyncio.create_task(runner.request_decision(tid,'intent',decision))
        await asyncio.sleep(0)
        try:await runner.send_input(tid,'y','different-choice')
        except RuntimeError as error:assert 'does not match' in str(error)
        else:assert False,'mismatched typed decision was accepted'
        await runner.send_input(tid,'y','live-choice');return await job
    assert asyncio.run(go())=='y'

def test_ui_state_doctrine_phase_gate_supersedes_stale_engine_prompt(tmp_path,pg_scoped_workspace):
    from delivery_api.control_snapshot import project
    store=pg_scoped_workspace.store;tid=store.create_task('Needs gate')['task_id']
    store.update_phase(tid,'intent','approved');store.update_phase(tid,'conops','approved');store.update_phase(tid,'needs','awaiting_approval');store.wait_at_gate(tid,'needs')
    task=store.task(tid);state=project(task,{'waiting':False,'process_live':False,'decision':None,'requirement':None})
    assert state.mode=='waiting_gate' and state.gate['after_phase']=='needs'
    assert state.mode=='waiting_gate' and state.gate is not None and state.decision is None


def test_ui_state_doctrine_live_decision_owns_input_over_waiting_delivery_gate(tmp_path,pg_scoped_workspace):
    from delivery_api.control_snapshot import project
    store=pg_scoped_workspace.store;tid=store.create_task('Overlapping input owners')['task_id']
    store.update_phase(tid,'intent','awaiting_approval');store.wait_at_gate(tid,'intent')
    task=store.task(tid)
    decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve this intent model?','options':[{'value':'y','label':'Yes'}]}
    state=project(task,{'waiting':True,'process_live':True,'decision':decision,'input_id':'intent-approval','requirement':None})
    assert state.mode=='waiting_input'
    assert state.decision==decision
    assert state.gate is None


def test_ui_state_doctrine_only_live_input_can_show_engine_decision():
    from delivery_api.control_snapshot import project
    decision={'type':'founder_decision','decision_id':'live','options':[{'value':'y','label':'Yes'}]}
    task={'task_id':'test-intent','state':'running','active_phase':'intent','gates':[]}
    live=project(task,{'waiting':True,'process_live':True,'decision':decision,'input_id':'intent-approval','requirement':None})
    stale=project(task,{'waiting':False,'process_live':False,'decision':decision})
    assert live.mode=='waiting_input' and live.decision==decision
    assert stale.mode=='failed' and stale.decision is None and stale.gate is None

def test_untyped_blocking_console_prompt_fails_visibly(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Untyped prompt')['task_id'];runner=ProcessRunner(store)
    # An untyped child blocking on stdin must not wait invisibly: the stall
    # bound stops it and fails the phase with a named cause.
    code=asyncio.run(runner.run(tid,[sys.executable,'-c','print("Continue? [y/N]",flush=True);input()'],cwd=tmp_path,stall_timeout=0.5))
    assert code!=0
    phase=next(p for p in store.task(tid)['phases'] if p['phase_key']=='intent')
    assert phase['state']=='failed'
    assert any(e['kind']=='error' and 'no output' in e['message'] for e in store.events(tid))

def test_split_envelope_chunks_stay_diagnostics(tmp_path,pg_scoped_workspace):
    import asyncio,json,sys
    from delivery_api.runner import ProcessRunner
    decision={'type':'founder_decision','decision_id':'intent-approval','title':'Approve intent','prompt':'Approve this intent model?','rationale_required':False,'options':[{'value':'y','label':'Approve'},{'value':'n','label':'Reject'}]}
    # Flush and pause force the envelope and console prompt into separate reads.
    script=f'''import json,time\nprint("REQUIRE_EVENT "+json.dumps({decision!r}),flush=True)\ntime.sleep(.15)\nprint("Approve this intent model? [y/N] ",end="",flush=True)'''
    store=pg_scoped_workspace.store;tid=store.create_task('Split envelope')['task_id']
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path))==0
    events=store.events(tid)
    assert not any(e['kind'] in ('founder_decision','waiting_input','error') for e in events)

def test_untyped_console_prompt_cannot_create_control_wait(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Parser fail')['task_id'];runner=ProcessRunner(store)
    code=asyncio.run(runner.run(tid,[sys.executable,'-c','import time;print("Approve this? [y/N]",flush=True);time.sleep(30)'],cwd=tmp_path,stall_timeout=0.5))
    assert code!=0 and store.task(tid)['state']=='failed' and tid not in runner.processes
    assert not any(e['kind']=='waiting_input' for e in store.events(tid))


def test_startup_resume_skips_historical_interview_after_durable_approval(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    store=pg_scoped_workspace.store;tid=store.create_task('Downstream already active')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    store.update_phase(tid,'intent','approved');store.update_phase(tid,'conops','approved');store.update_phase(tid,'needs','running')
    store.add_event(tid,'progress','session: completed-interview-checkpoint',phase_key='intent')
    monkeypatch.setattr(app_module,'store',store);monkeypatch.setitem(app_module.app.state._state,'store',store)
    captured=[]
    async def run(*args,**kwargs):captured.append((args,kwargs));return 0
    monkeypatch.setattr(app_module.runner,'run',run)
    async def go():await app_module._resume_interrupted_interviews();await asyncio.sleep(.01)
    asyncio.run(go())
    assert captured==[]

def test_startup_reconciles_contradictory_prefix_before_downstream_handoff(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    store=pg_scoped_workspace.store;tid=store.create_task('Corrupt recovered task')['task_id']
    store.update_phase(tid,'intent','running',detail='Waiting for founder answer')
    store.update_phase(tid,'needs','approved',detail='Approved by explicit open-gate policy')
    monkeypatch.setattr(app_module,'store',store);monkeypatch.setitem(app_module.app.state._state,'store',store)
    calls=[]
    async def continue_after(task_id,phase):calls.append((task_id,phase))
    monkeypatch.setattr(app_module.orchestrator,'continue_after',continue_after)
    async def go():await app_module._resume_orphaned_handoffs();await asyncio.sleep(.01)
    asyncio.run(go())
    states={p['phase_key']:p['state'] for p in store.task(tid)['phases']}
    assert states['intent']=='approved' and states['conops']=='approved' and states['needs']=='approved'
    assert calls==[(tid,'needs')]

def test_orchestrator_owned_decision_is_live_without_subprocess(tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Baseline')['task_id'];runner=ProcessRunner(store)
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    decision={'type':'founder_decision','decision_id':'system-baseline','title':'Choose baseline','prompt':'Which baseline?','options':[{'value':'greenfield','label':'Greenfield'}]}
    async def go():
        job=asyncio.create_task(runner.request_decision(tid,'architecture',decision))
        await asyncio.sleep(0);state=runner.input_state(tid)
        assert state=={'waiting':True,'process_live':True,'decision':decision,'requirement':None}
        await runner.send_input(tid,'greenfield','system-baseline');assert await job=='greenfield'
    asyncio.run(go())

def test_phase_configuration_is_canonical_and_complete():
    from delivery_api.phases import PHASE_DEFINITIONS,PHASE_BY_KEY,successor
    from delivery_api.store import PHASES
    assert tuple((p.key,p.label) for p in PHASE_DEFINITIONS)==PHASES
    assert [p.key for p in PHASE_DEFINITIONS]==['intent','conops','needs','srs','architecture','specs','tests','validation']
    assert [p.generator_stage for p in PHASE_DEFINITIONS[2:]]==['needs','srs','architecture','items','tests','validation']
    assert PHASE_BY_KEY['architecture'].prerequisite=='system-baseline'
    assert [successor(p.key).key if successor(p.key) else None for p in PHASE_DEFINITIONS]==['conops','needs','srs','architecture','specs','tests','validation',None]

def test_active_work_is_one_standard_projection_for_subprocess_and_decision(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Work')['task_id'];runner=ProcessRunner(store)
    decision={'type':'founder_decision','decision_id':'choice','title':'Choose','prompt':'Choose?','options':[{'value':'y','label':'Yes'}]}
    async def go():
        process=asyncio.create_task(runner.run(tid,[sys.executable,'-c','import time;time.sleep(.2)'],cwd=tmp_path))
        while runner.work_state(tid) is None:await asyncio.sleep(.001)
        work=runner.work_state(tid);assert work.owner=='subprocess' and work.status=='running' and runner.input_state(tid)['process_live']
        await process;assert runner.work_state(tid) is None
        pending=asyncio.create_task(runner.request_decision(tid,'architecture',decision));await asyncio.sleep(0)
        work=runner.work_state(tid);assert work.owner=='decision' and work.status=='waiting' and work.decision==decision
        assert runner.input_state(tid)==work.input_state()
        await runner.send_input(tid,'y','choice');assert await pending=='y';assert runner.work_state(tid) is None
    asyncio.run(go())

def test_recovery_planner_assigns_exactly_one_action_per_task():
    from delivery_api.recovery import plan_recovery
    phases=[{'phase_key':k,'state':'pending'} for k in ('intent','conops','needs','srs','architecture','specs','tests')]
    phases[0]['state']='approved';phases[1]['state']='approved';phases[2]['state']='approved'
    task={'state':'running','phases':phases}
    plan=plan_recovery(task,has_session=True)
    assert (plan.action,plan.phase,plan.target)==('launch_successor','needs','srs')
    phases[0]['state']='running'
    assert plan_recovery(task,has_session=True).action=='reconcile'
    for p in phases:p['state']='pending'
    assert plan_recovery(task,has_session=True).action=='resume'

def test_configured_phase_executor_rejects_wrong_owner_and_maps_redo(monkeypatch,tmp_path):
    import asyncio,json
    from delivery_api.orchestrator import ExecutionMode,PhaseOrchestrator
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'executor.db'));tid=store.create_task('Executor')['task_id']
    store.add_event(tid,'progress','session: configured-executor',phase_key='intent')
    root=tmp_path/'artifacts'/'configured-executor';(root/'interview').mkdir(parents=True);(root/'interview'/'intent_model.json').write_text(json.dumps({'contract_version':'1'}));(root/'interview'/'conops.json').write_text('{}')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'));monkeypatch.setenv('REQUI_RE_DIST','1')
    calls=[]
    class Runner:
        async def run(self,*args,**kwargs):calls.append((args,kwargs));return 0
    orchestrator=PhaseOrchestrator(store,Runner())
    asyncio.run(orchestrator.execute_phase(tid,'srs',mode=ExecutionMode.REDO))
    command=calls[0][0][1];assert command[-2:]==['--from','srs'] and command[command.index('--to')+1]=='srs'
    try:asyncio.run(orchestrator.execute_phase(tid,'intent'))
    except ValueError as error:assert 'owned by interview' in str(error)
    else:assert False

def test_external_requirement_is_standard_live_work_with_actionable_input(tmp_path,pg_scoped_workspace):
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Existing system')['task_id'];runner=ProcessRunner(store)
    requirement={'kind':'external_input','title':'Baseline required','path':'/tmp/system-baseline.json','format':'SystemBaseline JSON'}
    runner.pause_for_requirement(tid,'architecture',requirement);work=runner.work_state(tid)
    assert work.owner=='requirement' and work.status=='waiting' and work.requirement==requirement
    assert runner.input_state(tid)=={'waiting':True,'process_live':True,'decision':None,'requirement':requirement}

def test_recovery_restores_durable_external_input_wait(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Existing')['task_id']
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','awaiting_approval',detail='Baseline required')
    requirement={'kind':'external_input','title':'Existing baseline','path':'/tmp/system-baseline.json','format':'SystemBaseline JSON'}
    store.add_event(tid,'requirement','Architecture paused',phase_key='architecture',payload=requirement)
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    async def go():await app_module._recover_tasks();await asyncio.sleep(.01)
    asyncio.run(go());work=runner.work_state(tid)
    assert work and work.owner=='requirement' and work.phase=='architecture' and work.requirement==requirement

def test_recovery_requirement_owns_task_despite_later_obsolete_process_noise(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('One owner')['task_id']
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','awaiting_approval',detail='Baseline required')
    requirement={'kind':'external_input','title':'Baseline required','path':str(tmp_path/'missing-system-baseline.json'),'format':'SystemBaseline JSON'}
    store.add_event(tid,'requirement','Architecture paused',phase_key='architecture',payload=requirement)
    store.add_event(tid,'command','Obsolete interview process started',phase_key='intent')
    store.add_event(tid,'waiting_input','Approve this intent model? [y/N]',phase_key='intent',payload={'waiting_input':True})
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    launched=[]
    async def continue_after(*args):launched.append(args)
    monkeypatch.setattr(app_module.orchestrator,'continue_after',continue_after)
    async def go():await app_module._recover_tasks();await asyncio.sleep(.01)
    asyncio.run(go());work=runner.work_state(tid)
    assert work and work.owner=='requirement' and work.requirement==requirement
    assert launched==[] and tid not in runner.processes

def test_recovery_migrates_legacy_baseline_pause_to_requirement_owner(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Legacy existing')['task_id']
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','awaiting_approval',detail='Existing-system baseline required before architecture')
    store.add_event(tid,'gate','Architecture paused for an existing SystemBaseline file',phase_key='architecture',payload={'cause':'old bespoke pause'})
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    monkeypatch.setattr(app_module.orchestrator,'_baseline_path',lambda task_id:tmp_path/'session'/'spec'/'system-baseline.json')
    async def go():await app_module._recover_tasks();await asyncio.sleep(.01)
    asyncio.run(go());work=runner.work_state(tid)
    assert work and work.owner=='requirement' and work.phase=='architecture'
    assert work.requirement['format']=='SystemBaseline JSON' and work.requirement['path'].endswith('system-baseline.json')
    migrated=[event for event in store.events(tid) if event['kind']=='requirement']
    assert len(migrated)==1 and migrated[0]['payload']['kind']=='external_input'
    asyncio.run(go())
    migrated=[event for event in store.events(tid) if event['kind']=='requirement']
    assert len(migrated)==1


def test_snapshot_events_keep_newest_durable_facts_and_recovery_sees_requirement(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Long history')['task_id']
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','awaiting_approval',detail='Baseline required')
    for index in range(510):store.add_event(tid,'output',f'old line {index}',phase_key='srs')
    requirement={'kind':'external_input','title':'Existing baseline','path':'/tmp/system-baseline.json','format':'SystemBaseline JSON'}
    requirement_id=store.add_event(tid,'requirement','Architecture paused',phase_key='architecture',payload=requirement)
    events=store.events(tid)
    assert len(events)==500 and events[-1]['id']==requirement_id and events[0]['id']>1
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    asyncio.run(app_module._recover_tasks());work=runner.work_state(tid)
    assert work and work.owner=='requirement' and work.requirement==requirement


def test_orphaned_running_ui_state_explains_missing_owner():
    from delivery_api.control_snapshot import project
    task={'task_id':'test-architecture','state':'running','active_phase':'architecture','gates':[]}
    state=project(task,{'waiting':False,'process_live':False,'decision':None,'requirement':None})
    assert state.mode=='failed'
    assert state.diagnostic=={'reason':'task has no live ActiveWork owner','task_state':'running','active_phase':'architecture'}


def test_greenfield_correction_recovers_session_from_recent_requirement_path(monkeypatch,tmp_path):
    generator=tmp_path/"generator";python=generator/".venv/bin/python";python.parent.mkdir(parents=True);python.touch()
    monkeypatch.setenv("SPEC_GENERATOR_DIR",str(generator))
    import asyncio,json
    from delivery_api.orchestrator import PhaseOrchestrator
    from delivery_api.store import SQLiteStore
    artifacts=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(artifacts))
    store=SQLiteStore(str(tmp_path/'greenfield-correction.db'));tid=store.create_task('Daily Habit')['task_id']
    session='20260919-163614-adc4';interview=artifacts/session/'interview';interview.mkdir(parents=True)
    (interview/'intent_model.json').write_text(json.dumps({'contract_version':'0.16.0'}));(interview/'conops.json').write_text('{}')
    store.add_event(tid,'progress',f'session: {session}',phase_key='intent')
    for index in range(510):store.add_event(tid,'output',f'line {index}',phase_key='srs')
    path=artifacts/session/'spec'/'system-baseline.json'
    requirement={'kind':'external_input','title':'Baseline required','path':str(path),'format':'SystemBaseline JSON'}
    store.add_event(tid,'requirement','Architecture paused',phase_key='architecture',payload=requirement)
    class Runner:
        active_work={tid:object()};calls=[]
        async def run(self,*args,**kwargs):self.calls.append((args,kwargs));return 0
    runner=Runner();orch=PhaseOrchestrator(store,runner)
    asyncio.run(orch.resolve_baseline_requirement(tid,'greenfield'))
    baseline=json.loads(path.read_text())
    assert baseline['mode']=='greenfield' and baseline['baseline_id']==session+'-greenfield-v1'
    assert runner.calls and runner.calls[0][1]['phase']=='architecture'


def test_requirement_resolution_logs_and_returns_actionable_unexpected_failure(monkeypatch,caplog):
    import asyncio,logging
    from fastapi import HTTPException
    from delivery_api import app as app_module
    from delivery_api.auth import Actor
    class Work:owner='requirement';phase='architecture'
    monkeypatch.setattr(app_module.runner,'work_state',lambda task_id:Work())
    async def fail(task_id,action):raise RuntimeError('generator launch exploded')
    monkeypatch.setattr(app_module.orchestrator,'resolve_baseline_requirement',fail)
    caplog.set_level(logging.ERROR,logger='delivery_api.http')
    try:asyncio.run(app_module.resolve_architecture_requirement('daily',app_module.RequirementResolution(action='greenfield'),Actor('shy','shy')))
    except HTTPException as exc:
        assert exc.status_code==500 and exc.detail['cause']=='RuntimeError: generator launch exploded'
        assert 'remains recoverable' in exc.detail['next']
    else:assert False
    assert 'architecture requirement resolution failed task=daily action=greenfield' in caplog.text
    assert 'RuntimeError: generator launch exploded' in caplog.text


def test_requirement_resolution_restores_owner_from_same_durable_snapshot(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.auth import Actor
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Migrated')['task_id']
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','awaiting_approval',detail='Existing-system baseline required before architecture')
    requirement={'kind':'external_input','title':'Baseline required','path':str(tmp_path/'artifacts'/'session-x'/'spec'/'system-baseline.json'),'format':'SystemBaseline JSON'}
    store.add_event(tid,'requirement','Architecture paused',phase_key='architecture',payload=requirement)
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    observed=[]
    async def resolve(task_id,action):
        work=runner.work_state(task_id);observed.append((work.owner,work.phase,work.requirement,action))
    monkeypatch.setattr(app_module.orchestrator,'resolve_baseline_requirement',resolve)
    result=asyncio.run(app_module.resolve_architecture_requirement(tid,app_module.RequirementResolution(action='greenfield'),Actor('shy','shy')))
    assert result=={'status':'resolved','action':'greenfield'}
    assert observed==[('requirement','architecture',requirement,'greenfield')]


def test_final_verification_waits_for_approval_and_finishes_task(tmp_path,pg_scoped_workspace):
    store=pg_scoped_workspace.store;tid=store.create_task('Final approval')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    store.update_phase(tid,'validation','awaiting_approval',detail='Generated; waiting for founder approval')
    gate=store.wait_at_gate(tid,'validation')
    assert gate and gate['gate_key']=='gate-validation'
    assert store.task(tid)['state']=='waiting_gate'
    store.decide_gate(tid,'gate-validation','approve',selected_option='Approve Validation')
    task=store.task(tid)
    assert task['state']=='done' and next(p for p in task['phases'] if p['phase_key']=='validation')['state']=='approved'


def test_existing_database_migrates_missing_final_verification_gate(tmp_path):
    from delivery_api.store import SQLiteStore
    path=tmp_path/'legacy-final-gate.db';store=SQLiteStore(str(path));tid=store.create_task('Legacy')['task_id']
    store._db.execute("DELETE FROM gates WHERE task_id=? AND after_phase='tests'",(tid,));store._db.commit();store._db.close()
    migrated=SQLiteStore(str(path));task=migrated.task(tid)
    assert any(g['after_phase']=='tests' and g['gate_key']=='gate-tests' for g in task['gates'])


def test_recovery_restores_final_approval_gate_owner(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio
    from delivery_api import app as app_module
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Final recovery')['task_id']
    for phase in ('intent','conops','needs','srs','architecture','specs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'tests','running',detail='Generating tests')
    store.update_phase(tid,'tests','awaiting_approval',detail='Generated; waiting for founder approval')
    assert store.task(tid)['state']=='running'
    runner=ProcessRunner(store);monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'runner',runner)
    asyncio.run(app_module._recover_tasks())
    task=store.task(tid);gate=next(g for g in task['gates'] if g['after_phase']=='tests')
    assert task['state']=='waiting_gate' and gate['state']=='waiting'

def test_task_artifacts_project_real_session_files_and_download_zip(monkeypatch,tmp_path,pg_scoped_workspace):
    import json
    from fastapi.testclient import TestClient
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module.config,'CLOUD_TENANTS',False)
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));store=pg_scoped_workspace.store;tid=store.create_task('Verified product')['task_id'];store.add_event(tid,'progress','session: session-a',phase_key='intent')
    (root/'session-a'/'interview').mkdir(parents=True);(root/'session-a'/'spec').mkdir();(root/'session-a'/'interview'/'intent_model.json').write_text('{}');(root/'session-a'/'spec'/'architecture.json').write_text(json.dumps({'contract_version':'0.16.0'}));(root/'session-a'/'spec'/'architecture.md').write_text('# Architecture\n\n- Web app')
    monkeypatch.setattr(app_module,'store',store)
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    records=app_module._task_artifact_snapshot(tid);assert {(x['phase_key'],x['name']) for x in records}=={('intent','intent_model.json'),('architecture','architecture.json')};assert all(x['present'] for x in records);assert all(x['validator'] for x in records);assert all(isinstance(x['validation_errors'],list) for x in records)
    client=TestClient(app_module.app);view=client.get(f'/api/tasks/{tid}/artifacts/architecture/architecture.json/view');assert view.status_code==200 and view.json()['format']=='markdown' and view.json()['display_name']=='architecture.md' and view.json()['raw_format']=='json'
    archive=client.get(f'/api/tasks/{tid}/artifacts.zip');assert archive.status_code==200 and archive.headers['content-type']=='application/zip'


def test_legacy_artifact_projection_recovers_session_from_requirement_path(monkeypatch,tmp_path,pg_scoped_workspace):
    import json
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module.config,'CLOUD_TENANTS',False)
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));store=pg_scoped_workspace.store;tid=store.create_task('Daily Habit')['task_id'];session='20260919-163614-adc4'
    for index in range(510):store.add_event(tid,'output',f'line {index}',phase_key='srs')
    store.add_event(tid,'requirement','baseline',phase_key='architecture',payload={'kind':'external_input','path':str(root/session/'spec'/'system-baseline.json')})
    (root/session/'spec').mkdir(parents=True);(root/session/'spec'/'architecture.json').write_text(json.dumps({'contract_version':'0.16.0'}))
    monkeypatch.setattr(app_module,'store',store)
    assert app_module._recorded_engine_session(tid)==session
    records=app_module._task_artifact_snapshot(tid);assert len(records)==1 and records[0]['name']=='architecture.json' and records[0]['validator']=='ArchitectureOverview'

def test_orchestrator_phase_override_reaches_engine_and_is_durable(monkeypatch,tmp_path):
    generator=tmp_path/"generator";python=generator/".venv/bin/python";python.parent.mkdir(parents=True);python.touch()
    monkeypatch.setenv("SPEC_GENERATOR_DIR",str(generator))
    import asyncio,json
    from delivery_api.orchestrator import PhaseOrchestrator,ExecutionMode
    from delivery_api.store import SQLiteStore
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
    store=SQLiteStore(str(tmp_path/'route.db'));tid=store.create_task('Routed')['task_id'];store.set_model_route(tid,'ollama');store.add_event(tid,'progress','session: routed',phase_key='intent')
    interview=tmp_path/'artifacts'/'routed'/'interview';interview.mkdir(parents=True);(interview/'intent_model.json').write_text(json.dumps({'contract_version':'0.16.0'}));(interview/'conops.json').write_text('{}')
    class Runner:
        calls=[]
        async def run(self,*a,**kw):self.calls.append(kw);return 0
    r=Runner();asyncio.run(PhaseOrchestrator(store,r).execute_phase(tid,'needs',mode=ExecutionMode.REDO,model_route='deepseek:deepseek-flash'))
    assert r.calls[0]['env']['SPEC_GENERATOR_MODEL_NEEDS']=='deepseek:deepseek-flash'
    event=[e for e in store.events(tid) if e['kind']=='model_route'][-1]
    assert event['payload']=={'route':'deepseek','resolved':'deepseek:deepseek-flash','provider':'deepseek','model':'deepseek-flash','mode':'redo','source':'phase selection'}

def test_intent_artifact_view_has_content_title_and_interview_transcript(monkeypatch,tmp_path,pg_scoped_workspace):
    import json
    from fastapi.testclient import TestClient
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module.config,'CLOUD_TENANTS',False)
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));store=pg_scoped_workspace.store;tid=store.create_task('Habit app')['task_id'];store.add_event(tid,'progress','session: interview-view',phase_key='intent')
    folder=root/'interview-view'/'interview';folder.mkdir(parents=True);(folder/'intent_model.json').write_text(json.dumps({'problem_statement':'Build habits'}));(folder/'intent_model.md').write_text('# What we understood\n\n**Problem:** Build habits')
    store.add_event(tid,'waiting_input','You:',phase_key='intent',payload={'question':'Who needs this?','waiting_input':True});store.add_event(tid,'answer','You: Busy adults',phase_key='intent',payload={'answer':'Busy adults','transcript':True})
    monkeypatch.setattr(app_module,'store',store)
    body=TestClient(app_module.app).get(f'/api/tasks/{tid}/artifacts/intent/intent_model.json/view').json()
    assert body['title']=='Needs and Scope' and body['format']=='markdown'
    assert '**Problem:** Build habits' in body['content']
    assert '## Interview questions and answers' in body['content'] and 'Who needs this?' in body['content'] and '**Answer:** Busy adults' in body['content']

def test_price_execution_subtracts_llm_time_and_uses_configured_rates(monkeypatch,tmp_path):
    import json,importlib
    monkeypatch.setenv('PRICE_PER_TOKEN','2');monkeypatch.setenv('PRICE_PER_10MS','1')
    from delivery_api import pricing
    importlib.reload(pricing)
    usage=tmp_path/'usage.jsonl';llm=tmp_path/'llm-calls.jsonl'
    usage.write_text(json.dumps({'stage':'needs','prompt_tokens':100,'completion_tokens':50})+'\n')
    llm.write_text(json.dumps({'event':'llm_call_end','latency_s':0.8})+'\n')
    row=pricing.price_execution(usage,llm,1000)[0]
    assert row['tokens']==150 and row['deterministic_ms']==200
    assert row['token_price']==300 and row['infra_price']==20 and row['price']==320
    assert row['rates']=={'price_per_token':2.0,'price_per_10ms':1.0,'usd_per_credit':1.0,'scale':'credits'} and row['price_usd']==320
    assert row['cost_usd']==0 and row['unpriced_calls']==1  # row had no recorded rates

def test_price_execution_computes_usd_from_recorded_rates(tmp_path):
    import json
    from delivery_api import pricing
    usage=tmp_path/'u.jsonl';llm=tmp_path/'l.jsonl';llm.write_text('')
    rows=[{'stage':'needs','prompt_tokens':1000000,'completion_tokens':500000,'price_per_mtok_prompt_usd':0.3,'price_per_mtok_completion_usd':1.2},
          {'stage':'needs','prompt_tokens':900,'completion_tokens':100,'price_per_mtok_prompt_usd':0.0,'price_per_mtok_completion_usd':0.0}]
    usage.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    row=pricing.price_execution(usage,llm,0)[0]
    assert row['cost_usd']==0.9 and row['unpriced_calls']==0

def test_default_rates_put_a_full_run_near_five_credits(monkeypatch,tmp_path):
    import json,importlib
    monkeypatch.delenv('PRICE_PER_TOKEN',raising=False);monkeypatch.delenv('PRICE_PER_10MS',raising=False)
    from delivery_api import pricing
    importlib.reload(pricing)
    usage=tmp_path/'usage.jsonl';llm=tmp_path/'llm.jsonl'
    usage.write_text(json.dumps({'stage':'needs','prompt_tokens':320000,'completion_tokens':80000})+'\n');llm.write_text('')
    row=pricing.price_execution(usage,llm,0)[0]
    assert row['token_price']==5.0 and row['price']==5.0 and row['price_usd']==5.0

def test_runner_emits_durable_phase_price(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio,json,sys
    from delivery_api.runner import ProcessRunner
    monkeypatch.setenv('PRICE_PER_TOKEN','2');monkeypatch.setenv('PRICE_PER_10MS','0')
    from delivery_api import pricing
    monkeypatch.setattr(pricing,'PRICE_PER_TOKEN',2.0);monkeypatch.setattr(pricing,'PRICE_PER_10MS',0.0)
    out=tmp_path/'spec';out.mkdir();script="import json,sys,pathlib;p=pathlib.Path(sys.argv[1]);(p/'usage.jsonl').write_text(json.dumps({'stage':'needs','prompt_tokens':3,'completion_tokens':2})+'\\n')"
    store=pg_scoped_workspace.store;tid=store.create_task('Priced')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    code=asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script,str(out),'--out',str(out)],cwd=tmp_path,phase='needs',infer_phase=False))
    assert code!=0  # Pricing remains durable, but missing evidence blocks the gate.
    event=[e for e in store.events(tid) if e['kind']=='price'][-1]
    assert event['phase_key']=='needs' and event['payload']['tokens']==5 and event['payload']['price']==10
    audit=[e for e in store.audit_entries(tid) if e['action']=='phase.priced'][-1]
    assert audit['detail']['phase']=='needs' and audit['detail']['price']==10

def test_legacy_intent_view_recovers_full_transcript_from_checkpoint(monkeypatch,tmp_path):
    import json
    from fastapi.testclient import TestClient
    from delivery_api import app as app_module
    from delivery_api.store import SQLiteStore
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));store=SQLiteStore(str(tmp_path/'legacy-transcript.db'));tid=store.create_task('Legacy transcript')['task_id'];store.add_event(tid,'progress','session: legacy-turns',phase_key='intent')
    folder=root/'legacy-turns'/'interview';folder.mkdir(parents=True);(folder/'intent_model.json').write_text('{}');(folder/'intent_model.md').write_text('# What we understood')
    store.add_event(tid,'waiting_input','You:',phase_key='intent',payload={'question':'Old question?','waiting_input':True});store.add_event(tid,'answer','Interview answer submitted',phase_key='intent',payload={'character_count':9})
    monkeypatch.setattr(app_module,'store',store);monkeypatch.setattr(app_module,'_checkpoint_interview_turns',lambda _:[('Who needs this?','Busy adults'),('What matters?','Easy scheduling')])
    body=TestClient(app_module.app).get(f'/api/tasks/{tid}/artifacts/intent/intent_model.json/view').json()
    assert 'Who needs this?' in body['content'] and 'Busy adults' in body['content'] and 'Old question?' not in body['content']

def test_artifact_snapshot_exposes_content_owned_titles(monkeypatch,tmp_path,pg_scoped_workspace):
    import json
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module.config,'CLOUD_TENANTS',False)
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));store=pg_scoped_workspace.store;tid=store.create_task('Titles')['task_id'];store.add_event(tid,'progress','session: titles',phase_key='intent')
    folder=root/'titles'/'interview';folder.mkdir(parents=True);(folder/'intent_model.json').write_text('{}');(folder/'conops.json').write_text('{}')
    monkeypatch.setattr(app_module,'store',store)
    rows={r['name']:r['title'] for r in app_module._task_artifact_snapshot(tid)}
    assert rows=={'intent_model.json':'Needs and Scope','conops.json':'Concept of Operations'}

def test_runner_emits_incremental_interview_price_before_process_exit(monkeypatch,tmp_path,pg_scoped_workspace):
    import asyncio,json,sys,time
    from delivery_api.runner import ProcessRunner
    from delivery_api import pricing
    monkeypatch.setattr(pricing,'PRICE_PER_TOKEN',2.0);monkeypatch.setattr(pricing,'PRICE_PER_10MS',0.0);monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
    session='live-price';folder=tmp_path/'artifacts'/session/'interview';folder.mkdir(parents=True)
    script="import json,pathlib,time; p=pathlib.Path(%r); print('session: %s',flush=True); time.sleep(.05); (p/'usage.jsonl').write_text(json.dumps({'stage':'probing','prompt_tokens':3,'completion_tokens':2})+'\\n'); print('Question?\\nYou:',end='',flush=True); input(); print('done',flush=True)"%(str(folder),session)
    store=pg_scoped_workspace.store;tid=store.create_task('Live price')['task_id'];runner=ProcessRunner(store)
    async def scenario():
        work=asyncio.create_task(runner.run(tid,[sys.executable,'-c',script],cwd=tmp_path,phase='intent'))
        for _ in range(100):
            if tid in runner.waiting:break
            await asyncio.sleep(.01)
        await runner.send_input(tid,'answer')
        priced=[e for e in store.events(tid) if e['kind']=='price']
        assert priced and priced[-1]['payload']['tokens']==5 and priced[-1]['payload']['price']==10
        return await work
    assert asyncio.run(scenario())==0

def test_interviewer_prefix_lines_are_diagnostics_not_questions(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Prefix')['task_id']
    script="print('Interviewer: What change matters?\\nYou:',end='',flush=True)"
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path))==0
    assert not any(e['kind']=='waiting_input' for e in store.events(tid))

def test_answer_feedback_crud_and_audit(pg_workspace_factory,pg_task_app):
    from delivery_api import app as module
    from delivery_api.postgres_store import PostgresStore
    with pg_workspace_factory() as workspace,pg_workspace_factory() as other:
        api=pg_task_app(workspace)
        assert isinstance(module.store,PostgresStore)
        client=TestClient(api,headers=workspace.headers)
        task_id = client.post("/api/tasks", json={"title": "Feedback flow"}).json()["task_id"]

        empty = client.post(f"/api/tasks/{task_id}/feedback", json={"input_ref": "abc123", "target": "question"})
        assert empty.status_code == 400

        made = client.post(f"/api/tasks/{task_id}/feedback", json={
            "input_ref": "abc123", "target": "question",
            "content": "What should the family see first?", "thumb": -1, "comment": "Too vague"})
        assert made.status_code == 200
        row = made.json()
        assert row["thumb"] == -1 and row["comment"] == "Too vague" and row["actor"]
        fid = row["id"]
        foreign=TestClient(api,headers=other.headers)
        assert foreign.get(f'/api/tasks/{task_id}/feedback').status_code==404
        assert foreign.patch(f'/api/tasks/{task_id}/feedback/{fid}',json={'thumb':1}).status_code==404
        assert foreign.delete(f'/api/tasks/{task_id}/feedback/{fid}').status_code==404

        upsert = client.post(f"/api/tasks/{task_id}/feedback", json={
            "input_ref": "abc123", "target": "question", "content": "What should the family see first?", "thumb": 1})
        assert upsert.json()["id"] == fid and upsert.json()["thumb"] == 1 and upsert.json()["comment"] is None

        other_target = client.post(f"/api/tasks/{task_id}/feedback", json={
            "input_ref": "abc123", "target": "answer", "thumb": -1})
        assert other_target.json()["id"] != fid

        listing = client.get(f"/api/tasks/{task_id}/feedback").json()["feedback"]
        assert len(listing) == 2

        patched = client.patch(f"/api/tasks/{task_id}/feedback/{fid}", json={"thumb": None, "comment": "kept note"})
        assert patched.json()["thumb"] is None and patched.json()["comment"] == "kept note"

        missing = client.patch(f"/api/tasks/{task_id}/feedback/99999", json={"thumb": 1})
        assert missing.status_code == 404

        deleted = client.delete(f"/api/tasks/{task_id}/feedback/{fid}")
        assert deleted.status_code == 200
        assert len(client.get(f"/api/tasks/{task_id}/feedback").json()["feedback"]) == 1

        actions = [e["action"] for e in client.get(f"/api/tasks/{task_id}/audit").json()["entries"]]
        assert "feedback.upsert" in actions and "feedback.update" in actions and "feedback.delete" in actions


def test_task_classifications_join_shadow_to_answers(tmp_path):
    import json as _json
    from delivery_api.app import store as app_store
    session = "chip-session"
    root = tmp_path / session / "interview"
    root.mkdir(parents=True)
    os.environ["VMODEL_ARTIFACTS_DIR"] = str(tmp_path)
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"title": "Chip join"}).json()["task_id"]
        app_store.set_engine_session(task_id, session)
        q, a = "What should the family see first?", "A calm shared view"
        app_store.add_event(task_id, "waiting_input", q, phase_key="conops", payload={"waiting_input": True, "question": q})
        app_store.add_event(task_id, "answer", f"You: {a}", phase_key="conops", payload={"answer": a})
        rec = {"slot": "coverage", "timestamp": "t", "input": {"state": f"Question: {q}\nAnswer: {a}"},
               "baseline": {"status": "ok", "verdict": {"relevance": "relevant"}},
               "jev": {"status": "ok", "verdict": {"relevance": "thin"}}}
        rec2 = {"slot": "intent", "timestamp": "t", "input": {"state": f"Question: {q}\nFounder answer: {a}"},
                "baseline": {"status": "ok", "verdict": {"intent": "answer"}},
                "jev": {"status": "ok", "verdict": {"intent": "answer"}}}
        (root / "categorization-shadow.jsonl").write_text(_json.dumps(rec) + "\n" + _json.dumps(rec2) + "\n")

        data = client.get(f"/api/tasks/{task_id}/classifications").json()
        assert data["session"] == session and len(data["answers"]) == 1
        entry = next(iter(data["answers"].values()))
        assert entry["coverage"]["differed"] is True
        assert entry["coverage"]["jev"]["relevance"] == "thin" and entry["coverage"]["baseline"]["relevance"] == "relevant"
        assert entry["intent"]["differed"] is False

        no_session = client.post("/api/tasks", json={"title": "No session"}).json()["task_id"]
        assert client.get(f"/api/tasks/{no_session}/classifications").json()["answers"] == {}

def test_silent_engine_run_is_stopped_and_marked_failed(tmp_path,pg_scoped_workspace):
    import asyncio, sys
    from delivery_api.runner import ProcessRunner
    store = pg_scoped_workspace.store; tid = store.create_task('Silent engine')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    code = asyncio.run(ProcessRunner(store).run(
        tid, [sys.executable, '-c', 'import time;print("starting",flush=True);time.sleep(30)'],
        cwd=tmp_path, stall_timeout=0.5))
    assert code != 0
    phase = next(p for p in store.task(tid)['phases'] if p['phase_key'] == 'intent')
    assert phase['state'] == 'failed'
    errors = [e for e in store.events(tid) if e['kind'] == 'error']
    assert errors and 'no output' in errors[-1]['message'] and 'stopped' in errors[-1]['message']


def test_engine_heartbeats_reset_the_stall_window(tmp_path,pg_scoped_workspace):
    import asyncio, sys
    from delivery_api.runner import ProcessRunner
    store = pg_scoped_workspace.store; tid = store.create_task('Chatty engine')['task_id']
    script = 'import time\nfor i in range(4):\n print("tick", i, flush=True)\n time.sleep(0.3)'
    code = asyncio.run(ProcessRunner(store).run(tid, [sys.executable, '-c', script], cwd=tmp_path, stall_timeout=1))
    assert code == 0


def test_runner_preserves_module_name_when_subprocess_read_splits_mid_word(tmp_path,monkeypatch,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store;tid=store.create_task('Framing')['task_id']
    logs=[];monkeypatch.setattr('delivery_api.runner.activity',lambda kind,message,**fields:logs.append((kind,fields)))
    # The first write is larger than the runner's read window and deliberately
    # ends in an unfinished exception line. Both fragments must be one record.
    script='''import sys,time
sys.stdout.write("x"*470+"\\nModuleNotFoundError: No module named \'langgr")
sys.stdout.flush();time.sleep(.08)
sys.stdout.write("aph\'\\n");sys.stdout.flush();sys.exit(1)'''
    code=asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path))
    assert code==1
    output=[fields['line'] for kind,fields in logs if kind=='engine.output']
    assert "ModuleNotFoundError: No module named 'langgraph'" in output
    assert not any(line=="aph'" or line.endswith("'langgr") for line in output)
    error=[event for event in store.events(tid) if event['kind']=='error'][-1]
    assert error['payload']['module']=='langgraph'


def test_generator_failure_names_needs_not_interview_engine(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store
    tid=store.create_task('Generator error label')['task_id']
    command=[sys.executable,'-c','print("Error: incompatible contract",flush=True);raise SystemExit(1)']
    assert asyncio.run(ProcessRunner(store).run(tid,command,cwd=tmp_path,phase='needs',infer_phase=False))==1
    error=next(e for e in reversed(store.events(tid)) if e['kind']=='error')
    assert 'needs subprocess stopped with exit code 1' in error['message']
    assert 'interview engine' not in error['message'].lower()


def test_generator_failure_preserves_reported_contract_error(tmp_path,pg_scoped_workspace):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    store=pg_scoped_workspace.store
    tid=store.create_task('Contract error')['task_id']
    script='print("Error: intent contract 0.19.0 cannot be read by generator contract 0.16.x",flush=True);raise SystemExit(1)'
    assert asyncio.run(ProcessRunner(store).run(tid,[sys.executable,'-c',script],cwd=tmp_path,phase='needs',infer_phase=False))==1
    error=next(e for e in reversed(store.events(tid)) if e['kind']=='error')
    assert 'intent contract 0.19.0 cannot be read' in error['message']
    assert error['payload']['cause']=='Error: intent contract 0.19.0 cannot be read by generator contract 0.16.x'


def test_item_stage_list_artifact_validates_for_parked_gate(monkeypatch,tmp_path,pg_scoped_workspace):
    """The generator writes items.json as a list of ItemSpec, not SpecBundle."""
    import json
    from fastapi.testclient import TestClient
    from delivery_api import app as app_module
    monkeypatch.setattr(app_module.config,'CLOUD_TENANTS',False)
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root))
    store=pg_scoped_workspace.store;task_id=store.create_task('Item gate')['task_id']
    store.set_engine_session(task_id,'session-items')
    spec=root/'session-items'/'spec';spec.mkdir(parents=True)
    artifact=spec/'items.json'
    artifact.write_text(json.dumps([{'spec_id':'SPEC-1','kind':'ui_flow','title':'Status screen',
                                     'body_markdown':'# Status screen\nShow task status.','requirement_ids':['R-1']}]))
    monkeypatch.setattr(app_module,'store',store)
    records=app_module._task_artifact_snapshot(task_id)
    item=next(a for a in records if a['name']=='items.json')
    assert item['phase_key']=='specs' and item['present'] is True
    assert item['valid'] is True and item['validator']=='ItemSpec[]' and item['validation_errors']==[]
    client=TestClient(app_module.app)
    response=client.get(f'/api/tasks/{task_id}/artifacts/specs/items.json/view')
    assert response.status_code==200 and json.loads(response.json()['content'])[0]['spec_id']=='SPEC-1'
    artifact.write_text(json.dumps([{'spec_id':'BROKEN','kind':'ui_flow','title':'Status screen'}]))
    invalid=next(a for a in app_module._task_artifact_snapshot(task_id) if a['name']=='items.json')
    assert invalid['valid'] is False and invalid['validation_errors']


def test_orchestrator_sticky_exact_model_across_phases_and_switch(monkeypatch,tmp_path):
    import asyncio,json
    from delivery_api.orchestrator import PhaseOrchestrator
    from delivery_api.store import SQLiteStore
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))
    monkeypatch.setenv('REQUI_RE_DIST','1')
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_NEEDS','ollama:phi4')
    monkeypatch.setenv('SPEC_GENERATOR_MODEL_SRS','ollama:qwen')
    store=SQLiteStore(str(tmp_path/'route.db'));tid=store.create_task('Sticky')['task_id']
    store.set_engine_session(tid,'session-sticky')
    interview=tmp_path/'artifacts'/'session-sticky'/'interview';interview.mkdir(parents=True)
    (interview/'intent_model.json').write_text(json.dumps({'contract_version':'0.16.0'}))
    store.set_model_default(tid,'deepseek:deepseek-flash')
    class Runner:
        def __init__(self):self.calls=[]
        async def run(self,*a,**kw):self.calls.append(kw);return 0
    r=Runner();o=PhaseOrchestrator(store,r)
    asyncio.run(o.execute_phase(tid,'needs'))
    asyncio.run(o.execute_phase(tid,'srs'))
    assert r.calls[0]['env']['SPEC_GENERATOR_MODEL_NEEDS']=='deepseek:deepseek-flash'
    assert r.calls[1]['env']['SPEC_GENERATOR_MODEL_SRS']=='deepseek:deepseek-flash'
    assert 'deepseek:deepseek-flash' in [e for e in store.events(tid) if e['kind']=='model_route'][-1]['message']
    store.set_model_default(tid,'ollama:phi4')  # a confirmed phase-start selection
    asyncio.run(o.execute_phase(tid,'architecture',model_route='ollama:phi4'))
    asyncio.run(o.execute_phase(tid,'specs'))
    assert r.calls[2]['env']['SPEC_GENERATOR_MODEL_ARCHITECTURE']=='ollama:phi4'
    assert r.calls[3]['env']['SPEC_GENERATOR_MODEL_ITEMS']=='ollama:phi4'


def test_architecture_founder_refs_validate_in_delivery_snapshot_and_gate(monkeypatch,tmp_path):
    import json
    import delivery_api.app as app_module
    from delivery_api.store import SQLiteStore
    root=tmp_path/'artifacts';monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root))
    local=SQLiteStore(str(tmp_path/'refs.db'));tid=local.create_task('Founder source')['task_id']
    local.set_engine_session(tid,'founder-ref-session');monkeypatch.setattr(app_module,'store',local)
    folder=root/'founder-ref-session'/'spec';folder.mkdir(parents=True)
    payload={'contract_version':'0.27.0','session_id':'founder-ref-session','baseline_id':'baseline:test','stack_entries':[],'components':[],
             'decisions':[{'decision_id':'ADR-17','title':'Error surface','decision':'Display an error and reporting action','rationale':'Recorded founder decision','driver':'founder_constraint','options_considered':[],'founder_decision_refs':['FD-missing-error_states']}],'non_functionals':{}}
    (folder/'architecture.json').write_text(json.dumps(payload))
    artifact=next(a for a in app_module._task_artifact_snapshot(tid) if a['name']=='architecture.json')
    assert artifact['valid'] is True and artifact['validation_errors']==[], artifact['validation_errors']
    with TestClient(app_module.app) as client:
        read=client.get(f'/api/tasks/{tid}/artifacts/architecture/architecture.json/view')
    assert read.status_code==200
    assert json.loads(read.json()['content'])['decisions'][0]['founder_decision_refs']==['FD-missing-error_states']

def test_session_cost_uses_linked_task_retail_total(monkeypatch):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles, 'bundle', lambda session: {'session': {'session_id': session}})
    task = store.create_task('Cost linked', 'Test cost binding', task_id='cost-linked')
    store.set_engine_session('cost-linked', 'cost-session')
    store.add_event('cost-linked','price','Retail',phase_key='srs',payload={'price':2.5,'tokens':0,'rates':{'scale':'credits'}})
    with TestClient(app) as client:
        got=client.get('/api/sessions/cost-session/cost')
    assert got.status_code==200 and got.json()=={'price':2.5,'price_usd':2.5}


def test_session_cost_unknown_or_missing_usage_is_not_fabricated_zero(monkeypatch,tmp_path):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles, 'bundle', lambda session: {'session': {'session_id': session}})
    monkeypatch.setattr(app_module.bundles, 'root', lambda: tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/sessions/no-usage/cost').status_code==404


def test_session_cost_ambiguous_binding_rejected(monkeypatch):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles, 'bundle', lambda session: {'session': {'session_id': session}})
    for task in ('duplicate-cost-a','duplicate-cost-b'):
        store.create_task(task,'test',task_id=task);store.set_engine_session(task,'duplicate-cost')
    with TestClient(app) as client:
        assert client.get('/api/sessions/duplicate-cost/cost').status_code==409


def test_unlinked_interview_cost_is_retail_and_downstream_incomplete_is_unavailable(monkeypatch,tmp_path):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles,'bundle',lambda session:{'session':{'session_id':session}})
    monkeypatch.setattr(app_module.bundles,'root',lambda:tmp_path)
    session=tmp_path/'archived-cost';(session/'interview').mkdir(parents=True)
    (session/'interview'/'usage.jsonl').write_text('{"prompt_tokens":10000,"completion_tokens":10000}\n')
    with TestClient(app) as client:
        got=client.get('/api/sessions/archived-cost/cost')
        assert got.status_code==200 and got.json()=={'price':0.25,'price_usd':0.25}
        (session/'spec').mkdir();(session/'spec'/'usage.jsonl').write_text('{"prompt_tokens":1}\n')
        assert client.get('/api/sessions/archived-cost/cost').status_code==404


def test_unlinked_empty_usage_and_missing_downstream_usage_are_unavailable(monkeypatch,tmp_path):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles,'bundle',lambda session:{'session':{'session_id':session}})
    monkeypatch.setattr(app_module.bundles,'root',lambda:tmp_path)
    root=tmp_path/'empty-cost';(root/'interview').mkdir(parents=True)
    usage=root/'interview'/'usage.jsonl';usage.write_text('{}\n')
    with TestClient(app) as client:
        assert client.get('/api/sessions/empty-cost/cost').status_code==404
        usage.write_text('{"prompt_tokens":10,"completion_tokens":10}\n')
        (root/'spec').mkdir();(root/'spec'/'needs.json').write_text('{"needs":[]}')
        assert client.get('/api/sessions/empty-cost/cost').status_code==404


def test_session_cost_rejects_nonexistent_session(monkeypatch,tmp_path):
    import delivery_api.app as app_module
    monkeypatch.setattr(app_module.bundles,'root',lambda:tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/sessions/no-such-session/cost').status_code==404
