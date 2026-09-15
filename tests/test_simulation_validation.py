import json
from fastapi.testclient import TestClient
from delivery_api.app import app
from delivery_api.simulation import validate_recorded_run

def test_file_picker_upload_validates_and_imports():
    c=TestClient(app);rows=[{"session_id":"s","stage":"srs","provider":"ollama","model":"phi4","prompt_tokens":1,"completion_tokens":2,"ts":"2026-09-15T00:00:00Z"}]
    r=c.post('/api/simulations',files={'usage_file':('usage.jsonl',json.dumps(rows[0]).encode(),'application/json')},data={'title':'Replay'})
    assert r.status_code==201 and r.json()['simulated']

def test_import_rejects_wrong_name_and_malformed_event_with_guidance():
    c=TestClient(app)
    r=c.post('/api/simulations',files={'usage_file':('notes.txt',b'{}','text/plain')})
    assert r.status_code==400 and r.json()['detail']['next']
    r=c.post('/api/simulations',files={'usage_file':('usage.jsonl',b'{bad','text/plain')})
    d=r.json()['detail'];assert r.status_code==400 and all(d[k] for k in ('error','cause','next'))

def test_import_rejects_unknown_stage_and_mixed_sessions():
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


def test_simulate_endpoint_runs_on_asgi_event_loop(tmp_path):
    row={"stage":"scope_tiebreak","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"asgi"}
    with TestClient(app) as c:
        imported=c.post('/api/simulations',files={'usage_file':('usage.jsonl',(json.dumps(row)+'\n').encode(),'application/json')},data={'title':'ASGI replay'})
        assert imported.status_code==201
        r=c.post(f"/api/tasks/{imported.json()['task_id']}/simulate",json={'speed':0})
        assert r.status_code==200, r.text
        assert r.json()=={'status':'started','simulated':True,'speed':'instant'}


def test_simulate_start_failure_is_structured(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.setattr('delivery_api.app.simulation.start',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('loop unavailable')))
        r=c.post('/api/tasks/anything/simulate',json={'speed':0})
        assert r.status_code==500
        detail=r.json()['detail']
        assert set(detail)=={'error','cause','next'} and 'RuntimeError' in detail['cause']


def test_unified_session_import_infers_title_from_intent(tmp_path):
    from delivery_api.simulation import Simulation
    from delivery_api.store import SQLiteStore
    root=tmp_path/'meaningful-session'; interview=root/'interview';interview.mkdir(parents=True)
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"meaningful-session"}
    (interview/'usage.jsonl').write_text(json.dumps(row)+'\n')
    (interview/'intent_model.json').write_text(json.dumps({"problem_statement":"Help supply teams spot inventory risks"}))
    task=Simulation(SQLiteStore(str(tmp_path/'name.db'))).import_run(root)
    assert task['title']=='Help supply teams spot inventory risks'


def test_import_title_override_and_session_fallback(tmp_path):
    from delivery_api.simulation import Simulation
    from delivery_api.store import SQLiteStore
    source=tmp_path/'uploaded';source.mkdir()
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"s-42"}
    (source/'usage.jsonl').write_text(json.dumps(row)+'\n')
    sim=Simulation(SQLiteStore(str(tmp_path/'fallback.db')))
    assert sim.import_run(source)['title']=='Recorded run s-42'
    assert sim.import_run(source,'My replay')['title']=='My replay'


def test_uploaded_jsonl_resolves_local_canonical_intent(monkeypatch,tmp_path):
    from delivery_api.simulation import Simulation
    from delivery_api.store import SQLiteStore
    root=tmp_path/'artifacts';interview=root/'local-session'/'interview';interview.mkdir(parents=True)
    (interview/'intent_model.json').write_text(json.dumps({"problem_statement":"Track Pokémon collection goals"}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root))
    upload=tmp_path/'private-upload';upload.mkdir()
    row={"stage":"probing","provider":"ollama","model":"phi4","ts":"2026-09-15T10:00:00+03:00","prompt_tokens":1,"completion_tokens":1,"session_id":"local-session"}
    (upload/'usage.jsonl').write_text(json.dumps(row)+'\n')
    task=Simulation(SQLiteStore(str(tmp_path/'local.db'))).import_run(upload)
    assert task['title']=='Track Pokémon collection goals'
