"""First-run empty store is not a provider outage or an available local route."""
from types import SimpleNamespace
from delivery_api import app as m,cloud_runtime,model_pins


def test_empty_pins_raise_typed_reason(monkeypatch):
 monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[])
 factory=model_pins.DurableGateways(SimpleNamespace(_postgres=None),{})
 import pytest
 with pytest.raises(model_pins.NoApprovedModelPin):factory.refresh_launch()


def test_no_pin_closes_both_routes_and_explains_operator_repair(monkeypatch):
 monkeypatch.setattr(cloud_runtime,'_state',None)
 monkeypatch.setattr(cloud_runtime,'_failure_code','no_approved_model_pin',raising=False)
 monkeypatch.setattr(cloud_runtime,'_failure','internal private detail')
 reply=m.model_routes(SimpleNamespace(admin=True))
 assert reply['default_route'] is None
 for route in reply['routes']:
  assert route['available'] is False
  assert route['reason']=='no_approved_model_pin'
  assert route['error']=='No approved model pin. Approve one in Back office > Model pins.'
  assert route['operator_repair'] is True
  assert 'internal private' not in str(route)
 customer=m.model_routes(SimpleNamespace(admin=False))
 assert all('Back office' not in str(r) and not r.get('operator_repair') for r in customer['routes'])


def test_other_startup_failure_never_claims_ollama_available(monkeypatch):
 monkeypatch.setattr(cloud_runtime,'_state',None)
 monkeypatch.setattr(cloud_runtime,'_failure_code',None,raising=False)
 monkeypatch.setattr(cloud_runtime,'_failure','secret provider key')
 reply=m.model_routes(SimpleNamespace(admin=True))
 assert all(not route['available'] for route in reply['routes'])
 assert all(route['reason']=='runtime_unavailable' for route in reply['routes'])
 assert 'secret provider' not in str(reply)


def test_start_fence_names_pin_reason_without_opening_execution(monkeypatch):
 from test_engine_route_allowlist import client,post
 monkeypatch.setattr(cloud_runtime,'_state',None)
 monkeypatch.setattr(cloud_runtime,'_failure_code','no_approved_model_pin')
 with client() as c:
  r=post(c,'/api/tasks/t1/start')
  assert r.status_code==503 and r.json()['detail']['reason']=='no_approved_model_pin'


def test_install_records_typed_empty_pin_reason(monkeypatch):
 monkeypatch.setattr(cloud_runtime,'_state',None)
 monkeypatch.setattr(cloud_runtime,'_failure',None)
 monkeypatch.setattr(cloud_runtime,'_failure_code',None)
 monkeypatch.setattr(model_pins.PinRepository,'list',lambda self:[])
 import pytest
 with pytest.raises(model_pins.NoApprovedModelPin):
  cloud_runtime.install(SimpleNamespace(_postgres=None),{},lambda _:None)
 assert not cloud_runtime.installed()
 assert cloud_runtime.unavailable_reason()=='no_approved_model_pin'
