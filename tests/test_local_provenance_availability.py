from types import SimpleNamespace
from delivery_api import app as module


def test_checkpoint_absence_is_explicit_without_invented_turns(tmp_path,monkeypatch):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'_recorded_engine_session',lambda task:'session')
    monkeypatch.setattr(module,'authorized_session',lambda *args:tmp_path)
    status={}
    assert module._checkpoint_interview_turns('task',status)==[]
    assert status['checkpoint_transcript']=={'availability':'absent','reason':'no_saved_checkpoint'}


def test_corrupt_model_usage_is_not_empty_provenance(tmp_path,monkeypatch):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'_recorded_engine_session',lambda task:'session')
    monkeypatch.setattr(module,'authorized_session',lambda *args:tmp_path)
    monkeypatch.setattr(module,'store',SimpleNamespace(events=lambda task:[]))
    (tmp_path/'interview').mkdir();(tmp_path/'interview/usage.jsonl').write_text('fake-secret')
    status={}
    assert module._interview_model_provenance('task',status)==[]
    assert status['model_usage']['availability']=='unavailable'
    assert status['model_usage']['reason']=='invalid_or_unreadable'
    assert 'fake-secret' not in str(status)


def test_validation_supplement_status_does_not_hide_corruption(tmp_path,monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(module,'_recorded_engine_session',lambda task:None)
    def artifact(*args):raise HTTPException(404,'missing')
    monkeypatch.setattr(module,'_task_artifact',artifact)
    out=module._validation_panel_for('task','{}')
    assert out['supplemental_availability']['needs']['availability']=='absent'
    assert out['supplemental_availability']['tests']['availability']=='absent'
