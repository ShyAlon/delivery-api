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
