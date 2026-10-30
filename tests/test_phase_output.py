import json
import pytest
from fastapi.testclient import TestClient
from delivery_api import app as module

@pytest.fixture
def output_workspace(pg_scoped_workspace,monkeypatch):
    # PG output projection retains the existing local HTTP presentation contract.
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    return pg_scoped_workspace



def _case_partial_current_phase_and_labeled_previous_result(tmp_path,monkeypatch,output_workspace):
    from uuid import uuid4
    sid='output-'+uuid4().hex
    root=tmp_path/'artifacts';folder=root/sid/'spec';folder.mkdir(parents=True)
    store=output_workspace.store;tid=store.create_task('Output display','brief')['task_id']
    store.set_engine_session(tid,sid)
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGArtifact(store,tid,'needs.json','needs').write_text(json.dumps({'session_id':sid,'needs':[{'need_id':'N-1','stakeholder':'Caregiver','statement':'Visible progress','kind':'functional','intent_fields':['problem_statement']}]}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    client=TestClient(module.app)
    previous=client.get(f'/api/tasks/{tid}/phase-output').json()
    assert previous['status']=='previous_phase' and previous['display_phase']=='needs'
    assert previous['items'][0]['need_id']=='N-1'
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGUsage(store,tid,'spec/srs_fills.partial.json').write_text(json.dumps({'R-1':{'statement':'Show progress'}}))
    current=client.get(f'/api/tasks/{tid}/phase-output').json()
    assert current['status']=='in_progress' and current['display_phase']=='srs'
    assert current['items']==[{'id':'R-1','statement':'Show progress'}]


def _case_architecture_partial_uses_generator_keys(tmp_path,monkeypatch,output_workspace):
    from uuid import uuid4
    sid='output-'+uuid4().hex
    root=tmp_path/'artifacts';folder=root/sid/'spec';folder.mkdir(parents=True)
    store=output_workspace.store;tid=store.create_task('Architecture partial','brief')['task_id']
    store.set_engine_session(tid,sid)
    for phase in ('intent','conops','needs','srs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'architecture','running')
    import os,time
    time.sleep(.02)
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGUsage(store,tid,'spec/architecture.partial.json').write_text(json.dumps({'stack_entries':{'database':{'value':'SQLite','status':'proposed'}},'component_bodies':{'API':{'responsibility':'Serve requests'}},'adr_bodies':{'ADR-1':{'decision':'Use SQLite'}}}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='in_progress' and data['display_phase']=='architecture'
    assert {item['id'] for item in data['items']}=={'database','API','ADR-1'}


def _case_empty_current_partial_keeps_labeled_previous_phase(tmp_path,monkeypatch,output_workspace):
    from uuid import uuid4
    sid='output-'+uuid4().hex
    root=tmp_path/'artifacts';folder=root/sid/'spec';folder.mkdir(parents=True)
    store=output_workspace.store;tid=store.create_task('Empty partial','brief')['task_id']
    store.set_engine_session(tid,sid)
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGArtifact(store,tid,'needs.json','needs').write_text(json.dumps({'session_id':sid,'needs':[{'need_id':'N-1','stakeholder':'Caregiver','statement':'Visible progress','kind':'functional','intent_fields':['problem_statement']}]}))
    import time
    time.sleep(.02)
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGUsage(store,tid,'spec/srs_fills.partial.json').write_text('{}')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='previous_phase' and data['display_phase']=='needs'


def _case_gate_redo_resets_phase_start_and_rejects_old_output(tmp_path,monkeypatch,output_workspace):
    from uuid import uuid4
    sid='output-'+uuid4().hex
    root=tmp_path/'artifacts';folder=root/sid/'spec';folder.mkdir(parents=True)
    store=output_workspace.store;tid=store.create_task('Redo freshness','brief')['task_id']
    store.set_engine_session(tid,sid)
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    _PGUsage(store,tid,'spec/srs_fills.partial.json').write_text(json.dumps({'R-old':{'statement':'Old requirement'}}))
    store.update_phase(tid,'srs','awaiting_approval')
    store.wait_at_gate(tid,'srs')
    import time
    time.sleep(.02)
    store.decide_gate(tid,'gate-srs','redo',steer='Change scope')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='waiting' and data['items']==[]


def _case_invalid_current_artifact_is_unavailable_not_previous(tmp_path,monkeypatch,output_workspace):
    from uuid import uuid4
    sid='output-'+uuid4().hex
    root=tmp_path/'artifacts';folder=root/sid/'spec';folder.mkdir(parents=True)
    store=output_workspace.store;tid=store.create_task('Invalid current','brief')['task_id']
    store.set_engine_session(tid,sid)
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    import time
    time.sleep(.02)
    from test_http_cutover import _PGUsage
    from test_stack_decisions import _PGArtifact
    store.publish_task_revision_bytes(tid,[{'phase_key':'srs','name':'srs.json','data':b'{not valid JSON','validation_report':{'sha256':__import__('hashlib').sha256(b'{not valid JSON').hexdigest(),'valid':False,'error':'invalid JSON','validator':'test-only','validator_version':'test-only'}}],writer_instance='test-only')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='unavailable' and data['items']==[]


def test_output_fixture_uses_restricted_pg(output_workspace):
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(module.store,PostgresStore)
    from delivery_api.store import SQLiteStore
    with pytest.raises(AssertionError,match='SQLite forbidden'):
        SQLiteStore(':memory:')
    with module.store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'


def _check_partial_current_phase_and_labeled_previous_result(monkeypatch,tmp_path,workspace):
    from tests.test_phase_output import _case_partial_current_phase_and_labeled_previous_result
    _case_partial_current_phase_and_labeled_previous_result(tmp_path,monkeypatch,workspace)


def test_partial_current_phase_and_labeled_previous_result(pg_local_compat_process):
    pg_local_compat_process(_check_partial_current_phase_and_labeled_previous_result)


def _check_architecture_partial_uses_generator_keys(monkeypatch,tmp_path,workspace):
    from tests.test_phase_output import _case_architecture_partial_uses_generator_keys
    _case_architecture_partial_uses_generator_keys(tmp_path,monkeypatch,workspace)


def test_architecture_partial_uses_generator_keys(pg_local_compat_process):
    pg_local_compat_process(_check_architecture_partial_uses_generator_keys)


def _check_empty_current_partial_keeps_labeled_previous_phase(monkeypatch,tmp_path,workspace):
    from tests.test_phase_output import _case_empty_current_partial_keeps_labeled_previous_phase
    _case_empty_current_partial_keeps_labeled_previous_phase(tmp_path,monkeypatch,workspace)


def test_empty_current_partial_keeps_labeled_previous_phase(pg_local_compat_process):
    pg_local_compat_process(_check_empty_current_partial_keeps_labeled_previous_phase)


def _check_gate_redo_resets_phase_start_and_rejects_old_output(monkeypatch,tmp_path,workspace):
    from tests.test_phase_output import _case_gate_redo_resets_phase_start_and_rejects_old_output
    _case_gate_redo_resets_phase_start_and_rejects_old_output(tmp_path,monkeypatch,workspace)


def test_gate_redo_resets_phase_start_and_rejects_old_output(pg_local_compat_process):
    pg_local_compat_process(_check_gate_redo_resets_phase_start_and_rejects_old_output)


def _check_invalid_current_artifact_is_unavailable_not_previous(monkeypatch,tmp_path,workspace):
    from tests.test_phase_output import _case_invalid_current_artifact_is_unavailable_not_previous
    _case_invalid_current_artifact_is_unavailable_not_previous(tmp_path,monkeypatch,workspace)


def test_invalid_current_artifact_is_unavailable_not_previous(pg_local_compat_process):
    pg_local_compat_process(_check_invalid_current_artifact_is_unavailable_not_previous)
