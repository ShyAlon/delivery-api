import json
from fastapi.testclient import TestClient
from delivery_api import app as module
from delivery_api.store import SQLiteStore


def test_partial_current_phase_and_labeled_previous_result(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-1'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'output.db'));tid=store.create_task('Output display','brief')['task_id']
    store.set_engine_session(tid,'session-1')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    (folder/'needs.json').write_text(json.dumps({'session_id':'session-1','needs':[{'need_id':'N-1','stakeholder':'Caregiver','statement':'Visible progress','kind':'functional','intent_fields':['problem_statement']}]}))
    store.add_task_artifact(tid,'needs','Stakeholder needs',str(folder/'needs.json'))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    client=TestClient(module.app)
    previous=client.get(f'/api/tasks/{tid}/phase-output').json()
    assert previous['status']=='previous_phase' and previous['display_phase']=='needs'
    assert previous['items'][0]['need_id']=='N-1'
    (folder/'srs_fills.partial.json').write_text(json.dumps({'R-1':{'statement':'Show progress'}}))
    current=client.get(f'/api/tasks/{tid}/phase-output').json()
    assert current['status']=='in_progress' and current['display_phase']=='srs'
    assert current['items']==[{'id':'R-1','statement':'Show progress'}]


def test_architecture_partial_uses_generator_keys(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-2'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'architecture.db'));tid=store.create_task('Architecture partial','brief')['task_id']
    store.set_engine_session(tid,'session-2')
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','running')
    import os,time
    time.sleep(.02)
    (folder/'architecture.partial.json').write_text(json.dumps({'stack_entries':{'database':{'value':'SQLite','status':'proposed'}},'component_bodies':{'API':{'responsibility':'Serve requests'}},'adr_bodies':{'ADR-1':{'decision':'Use SQLite'}}}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='in_progress' and data['display_phase']=='architecture'
    assert {item['id'] for item in data['items']}=={'database','API','ADR-1'}


def test_rerun_does_not_relabel_old_canonical_or_partial_as_current(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-3'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'rerun.db'));tid=store.create_task('Rerun freshness','brief')['task_id']
    store.set_engine_session(tid,'session-3')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    (folder/'srs.json').write_text(json.dumps({'functional':[]}))
    (folder/'srs_fills.partial.json').write_text(json.dumps({'R-old':{'statement':'Stale'}}))
    store.update_phase(tid,'srs','awaiting_approval')
    # A new generation begins, but the old files remain under the same session.
    store.prepare_rerun(tid,'srs')
    import time
    time.sleep(.02)
    store.update_phase(tid,'srs','running')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='waiting' and data['items']==[]


def test_empty_current_partial_keeps_labeled_previous_phase(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-4'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'empty.db'));tid=store.create_task('Empty partial','brief')['task_id']
    store.set_engine_session(tid,'session-4')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    (folder/'needs.json').write_text(json.dumps({'session_id':'session-4','needs':[{'need_id':'N-1','stakeholder':'Caregiver','statement':'Visible progress','kind':'functional','intent_fields':['problem_statement']}]}))
    import time
    time.sleep(.02)
    (folder/'srs_fills.partial.json').write_text('{}')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='previous_phase' and data['display_phase']=='needs'


def test_gate_redo_resets_phase_start_and_rejects_old_output(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-5'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'redo.db'));tid=store.create_task('Redo freshness','brief')['task_id']
    store.set_engine_session(tid,'session-5')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    (folder/'srs_fills.partial.json').write_text(json.dumps({'R-old':{'statement':'Old requirement'}}))
    store.update_phase(tid,'srs','awaiting_approval')
    store.wait_at_gate(tid,'srs')
    import time
    time.sleep(.02)
    store.decide_gate(tid,'gate-srs','redo',steer='Change scope')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='waiting' and data['items']==[]


def test_invalid_current_artifact_is_unavailable_not_previous(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-6'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'invalid.db'));tid=store.create_task('Invalid current','brief')['task_id']
    store.set_engine_session(tid,'session-6')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    import time
    time.sleep(.02)
    (folder/'srs.json').write_text('{not valid JSON')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='unavailable' and data['items']==[]
