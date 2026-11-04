import json
import pytest
from fastapi.testclient import TestClient
from delivery_api.app import app
from delivery_api.simulation import validate_recorded_run

@pytest.fixture
def simulation_workspace(pg_scoped_workspace,monkeypatch):
    from delivery_api import app as module
    from delivery_api.simulation import Simulation
    monkeypatch.setattr(module,'simulation',Simulation(pg_scoped_workspace.store))
    yield pg_scoped_workspace


def _case_file_picker_upload_validates_and_imports():
    c=TestClient(app);rows=[{"session_id":"s","stage":"srs","provider":"ollama","model":"phi4","prompt_tokens":1,"completion_tokens":2,"ts":"2026-09-15T00:00:00Z"}]
    r=c.post('/api/simulations',files={'usage_file':('usage.jsonl',json.dumps(rows[0]).encode(),'application/json')},data={'title':'Replay'})
    assert r.status_code==201 and r.json()['simulated']

def _case_import_rejects_wrong_name_and_malformed_event_with_guidance():
    c=TestClient(app)
    r=c.post('/api/simulations',files={'usage_file':('notes.txt',b'{}','text/plain')})
    assert r.status_code==400 and r.json()['detail']['next']
    r=c.post('/api/simulations',files={'usage_file':('usage.jsonl',b'{bad','text/plain')})
    d=r.json()['detail'];assert r.status_code==400 and all(d[k] for k in ('error','cause','next'))

def _case_import_rejects_unknown_stage_and_mixed_sessions():
    c=TestClient(app)
    bad={"stage":"bogus","provider":"p","model":"m","ts":"2026-09-15T00:00:00Z"}
    r=c.post('/api/simulations',files={'usage_file':('usage.jsonl',json.dumps(bad).encode(),'text/plain')})
    assert r.status_code==400 and 'unknown stage' in r.json()['detail']['error']


def test_interview_stage_vocabulary_and_unified_session_root(tmp_path):
    root=tmp_path/'session'; interview=root/'interview';interview.mkdir(parents=True)
    row={"stage":"scope_tiebreak","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"session"}
    (interview/'usage.jsonl').write_text(json.dumps(row)+'\n')
    rows,sid=validate_recorded_run(root)
    assert rows==[row] and sid=='session'


def _case_simulate_endpoint_runs_on_asgi_event_loop(tmp_path):
    row={"stage":"scope_tiebreak","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"asgi"}
    with TestClient(app) as c:
        imported=c.post('/api/simulations',files={'usage_file':('usage.jsonl',(json.dumps(row)+'\n').encode(),'application/json')},data={'title':'ASGI replay'})
        assert imported.status_code==201
        r=c.post(f"/api/tasks/{imported.json()['task_id']}/simulate",json={'speed':0})
        assert r.status_code==200, r.text
        assert r.json()=={'status':'started','simulated':True,'speed':'instant'}


def _case_simulate_start_failure_is_structured(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.setattr('delivery_api.app.simulation.start',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('loop unavailable')))
        r=c.post('/api/tasks/anything/simulate',json={'speed':0})
        assert r.status_code==500
        detail=r.json()['detail']
        assert set(detail)=={'error','cause','next'} and 'RuntimeError' in detail['cause']


def test_unified_session_import_infers_title_from_intent(tmp_path,simulation_workspace):
    from delivery_api.simulation import Simulation
    root=tmp_path/'meaningful-session'; interview=root/'interview';interview.mkdir(parents=True)
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"meaningful-session"}
    (interview/'usage.jsonl').write_text(json.dumps(row)+'\n')
    (interview/'intent_model.json').write_text(json.dumps({"problem_statement":"Help supply teams spot inventory risks"}))
    task=Simulation(simulation_workspace.store).import_run(root)
    assert task['title']=='Help supply teams spot inventory risks'


def test_import_title_override_and_session_fallback(tmp_path,simulation_workspace):
    from delivery_api.simulation import Simulation
    source=tmp_path/'uploaded';source.mkdir()
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"s-42"}
    (source/'usage.jsonl').write_text(json.dumps(row)+'\n')
    sim=Simulation(simulation_workspace.store)
    assert sim.import_run(source)['title']=='Recorded run s-42'
    assert sim.import_run(source,'My replay')['title']=='My replay'


def test_uploaded_jsonl_does_not_consult_unrelated_host_intent(monkeypatch,tmp_path,simulation_workspace):
    from delivery_api.simulation import Simulation
    root=tmp_path/'artifacts';interview=root/'local-session'/'interview';interview.mkdir(parents=True)
    (interview/'intent_model.json').write_text(json.dumps({"problem_statement":"Track Pokémon collection goals"}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root))
    upload=tmp_path/'private-upload';upload.mkdir()
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"local-session"}
    (upload/'usage.jsonl').write_text(json.dumps(row)+'\n')
    task=Simulation(simulation_workspace.store).import_run(upload)
    assert task['title']=='Recorded run local-session'


def test_simulation_fixture_uses_pg_for_http_and_replay(simulation_workspace):
    from delivery_api import app as module
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(module.store,PostgresStore)
    assert module.simulation.store is module.store
    from pg_storage_guard import assert_pg_storage_only
    assert_pg_storage_only()
    with module.store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'


@pytest.fixture
def simulation_helper(simulation_workspace,monkeypatch):
    # Exercise the private validated import/replay implementation directly.
    # Public simulation routes deliberately remain fenced everywhere.
    import asyncio
    from delivery_api import app as module
    from fastapi import UploadFile,HTTPException
    import io
    class Response:
        def __init__(self,status,body):self.status_code=status;self.body=body;self.text=str(body)
        def json(self):return self.body
    class Client:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,path,files=None,data=None,json=None):
            async def call():
                try:
                    if files:
                        name,raw,_=files['usage_file']
                        result=await module.import_simulation(UploadFile(io.BytesIO(raw),filename=name),(data or {}).get('title'))
                        return Response(201,result)
                    result=await module.replay_simulation(path.split('/')[3],module.ReplayRequest(**json))
                    return Response(200,result)
                except HTTPException as exc:return Response(exc.status_code,{'detail':exc.detail})
                finally:
                    for task in module.simulation.running.values():
                        if not task.done():task.cancel()
            return asyncio.run(call())
    monkeypatch.setitem(globals(),'TestClient',lambda *a,**kw:Client())


def test_private_upload_validates_and_imports(simulation_helper):
    _case_file_picker_upload_validates_and_imports()


def test_private_import_rejects_wrong_name_and_malformed_event_with_guidance(simulation_helper):
    _case_import_rejects_wrong_name_and_malformed_event_with_guidance()


def test_private_import_rejects_unknown_stage_and_mixed_sessions(simulation_helper):
    _case_import_rejects_unknown_stage_and_mixed_sessions()


def test_private_simulate_runs_on_event_loop(tmp_path,simulation_helper):
    _case_simulate_endpoint_runs_on_asgi_event_loop(tmp_path)


def test_private_simulate_start_failure_is_structured(monkeypatch,simulation_helper):
    _case_simulate_start_failure_is_structured(monkeypatch)


def test_public_simulation_is_unavailable_even_to_signed_owner(pg_task_app,simulation_workspace):
    c=TestClient(pg_task_app(simulation_workspace),headers=simulation_workspace.headers)
    assert c.post('/api/simulations',files={'usage_file':('usage.jsonl',b'{}','application/json')}).status_code==503
    assert c.post('/api/tasks/anything/simulate',json={'speed':0}).status_code==503
