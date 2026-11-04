"""Same installed runtime readiness, no location or opt-in selector."""
import pytest
from delivery_api import cloud_runtime,cloud_execution

def test_uninstalled_runtime_never_claims_ready():
 cloud_runtime.uninstall();cloud_runtime._failure=None
 for env in ({},{'DELIVERY_PUBLIC_ORIGIN':'https://remote.example'},{'DELIVERY_PUBLIC_ORIGIN':'http://localhost:8000'}):
  with pytest.raises(ValueError,match='not ready'):cloud_runtime.check_gateways(env)
  assert not cloud_execution.admitted(env)

def test_startup_failure_never_exposes_secrets():
 cloud_runtime.uninstall();cloud_runtime._failure='secret provider key'
 try:
  assert cloud_execution.problems()==['reviewed child runtime is not ready','child runtime startup failed']
 finally:cloud_runtime._failure=None
