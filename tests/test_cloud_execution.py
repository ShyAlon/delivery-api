"""Readiness is installed-state, never a deployment flag or spend grant."""
import pytest
from types import SimpleNamespace
from delivery_api import cloud_runtime,cloud_execution

@pytest.mark.parametrize('origin',['http://localhost:8000','https://remote.example'])
def test_same_ready_state_ignores_retired_flags(monkeypatch,origin):
 monkeypatch.setattr(cloud_runtime,'_failure',None)
 monkeypatch.setattr(cloud_runtime,'_state',(SimpleNamespace(models={'ollama':'reviewed'}),None,None))
 assert cloud_execution.admitted({'DELIVERY_PUBLIC_ORIGIN':origin,'DELIVERY_CLOUD_EXECUTION':'0','DELIVERY_CONTROL_PLANE_ONLY':'1'})
 monkeypatch.setattr(cloud_runtime,'_state',(SimpleNamespace(models={}),None,None))
 assert not cloud_execution.admitted({'DELIVERY_PUBLIC_ORIGIN':origin})
