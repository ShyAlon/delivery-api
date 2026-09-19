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


def test_process_runner_marks_input_prompts_distinct_from_work(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    store=SQLiteStore(str(tmp_path/'waiting.db'));task_id=store.create_task('Waiting prompt')['task_id']
    script="print('Generating interview question',flush=True);print('What matters?\\nYou:',end='',flush=True)"
    assert asyncio.run(ProcessRunner(store).run(task_id,[sys.executable,'-c',script],cwd=tmp_path))==0
    events=store.events(task_id)
    assert any(e['kind']=='progress' and not e['payload'].get('waiting_input') for e in events)
    waiting=next(e for e in events if e['kind']=='waiting_input')
    assert waiting['payload']['waiting_input'] is True and waiting['message'].endswith('You:')


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


def test_process_runner_hides_traceback_and_classifies_missing_module(tmp_path):
    import asyncio,sys
    from delivery_api.runner import ProcessRunner
    from delivery_api.store import SQLiteStore
    isolated=SQLiteStore(str(tmp_path/'failure.db'));task_id=isolated.create_task('Failure')['task_id']
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

def test_built_runtime_bypasses_interpreter_discovery(monkeypatch,tmp_path):
    from delivery_api.app import app,store
    from delivery_api.engine_runtime import EngineRuntime
    from fastapi.testclient import TestClient
    import sys,time
    task_id='dist-runtime-test'
    if store.task(task_id):store.delete_task(task_id)
    store.create_task('Built runtime','One execution path',task_id=task_id)
    async def forbidden(_):raise AssertionError('dist runtime must not discover interpreters')
    calls=[]
    async def fake_run(task_id,command,**kwargs):calls.append(command);return 0
    from delivery_api import auth
    auth.limiter=auth.RateLimiter()
    monkeypatch.setenv('REQUI_RE_DIST','1');monkeypatch.setattr('delivery_api.app.resolve_engine_runtime',forbidden);monkeypatch.setattr('delivery_api.app.runner.run',fake_run)
    response=TestClient(app).post(f'/api/tasks/{task_id}/start')
    assert response.status_code==200
    for _ in range(30):
        if calls:break
        time.sleep(.01)
    assert calls and calls[0][0]==sys.executable and calls[0][1:3]==['-m','interview_engine.cli']

def test_default_database_is_stable_user_data_outside_repo_and_dist(monkeypatch,tmp_path):
    import importlib
    from pathlib import Path
    from delivery_api import config
    monkeypatch.delenv('DELIVERY_DATABASE_URL',raising=False);monkeypatch.delenv('DELIVERY_DB_PATH',raising=False);monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'xdg'))
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
