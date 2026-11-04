from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from delivery_api import app as module


@pytest.mark.parametrize('confirm',[False,True])
def test_tenant_rerun_refuses_before_promising_preview_or_writing(monkeypatch,confirm):
    calls=[]
    store=SimpleNamespace(task=lambda tid:{'phases':[{'phase_key':'intent','position':0}]},
        rerun_prerequisite=lambda *args:None,prepare_rerun=lambda *args:calls.append(args))
    monkeypatch.setattr(module,'store',store)
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    with pytest.raises(HTTPException) as exc:
        module.rerun('task',module.RerunRequest(from_phase='intent',confirm=confirm),None,SimpleNamespace(label='owner'))
    assert exc.value.status_code==409
    assert exc.value.detail['code']=='tenant_rerun_unavailable'
    assert 'new-generation' in exc.value.detail['cause']
    assert 'No work changed' in exc.value.detail['next']
    assert not calls


def test_task_capability_is_explicit_for_tenant_and_local(monkeypatch):
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',True)
    assert module._rerun_control()['available'] is False
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    assert module._rerun_control()['available'] is False


def test_http_task_detail_includes_rerun_capability(pg_scoped_workspace,pg_task_app):
    from fastapi.testclient import TestClient
    with TestClient(pg_task_app(pg_scoped_workspace),headers=pg_scoped_workspace.headers) as client:
        tid=client.post('/api/tasks',json={'title':'Rerun capability '+__import__('uuid').uuid4().hex}).json()['task_id']
        control=client.get(f'/api/tasks/{tid}').json()['rerun_control']
        assert control['available'] is False and 'new-generation' in control['reason']
