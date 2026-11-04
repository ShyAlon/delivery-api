from types import SimpleNamespace
from delivery_api import app as module




def test_model_usage_policy_does_not_read_host_or_infer_empty_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'store',SimpleNamespace())
    status={}
    assert module._interview_model_provenance('task',status)==[]
    assert status['model_usage']=={'availability':'policy_excluded','reason':'provider_usage_excluded'}


def test_validation_supplement_status_does_not_hide_corruption(tmp_path,monkeypatch):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'store',SimpleNamespace(read_artifact_bytes=lambda *args: (_ for _ in ()).throw(KeyError('missing'))))
    from fastapi import HTTPException
    monkeypatch.setattr(module,'_recorded_engine_session',lambda task:None)
    def artifact(*args):raise HTTPException(404,'missing')
    out=module._validation_panel_for('task','{}')
    assert out['supplemental_availability']['needs']['availability']=='absent'
    assert out['supplemental_availability']['tests']['availability']=='absent'
