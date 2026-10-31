"""Hermetic checks of cloud runtime start-up. Fakes only; no network, no provider, no database."""
import sys,types
import pytest
from delivery_api import cloud_runtime,cloud_execution,config,inprocess_engine,production_gateway as pg
from delivery_api.attempt_ledger import AdmissionDenied
import importlib.util,pathlib
_spec=importlib.util.spec_from_file_location('gateway_test_inputs',pathlib.Path(__file__).with_name('test_production_gateway.py'))
_inputs=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_inputs)
gateway_env=_inputs.env


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cloud_runtime.uninstall();cloud_runtime._failure=None;cloud_runtime._checked.clear()
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.delenv('DELIVERY_CONTROL_PLANE_ONLY',raising=False)
    yield
    cloud_runtime.uninstall();cloud_runtime._failure=None


def test_check_gateways_accepts_a_full_reviewed_environment_and_refuses_a_partial_one():
    with pytest.raises(ValueError,match='activation is pending'):cloud_runtime.check_gateways(gateway_env())
    e=gateway_env();e.pop(pg.CEILING_ENV)
    with pytest.raises((AdmissionDenied,ValueError)):cloud_runtime.check_gateways(e)


def test_removed_default_cannot_install_or_select_a_client():
    chosen=[]
    with pytest.raises(ValueError,match='activation is pending'):
        cloud_runtime.install(object(),gateway_env(),chosen.append)
    assert chosen==[] and not cloud_runtime.installed() and inprocess_engine.current() is None


def good():
    return {**gateway_env(),'DELIVERY_CLOUD_EXECUTION':'1','INTERVIEW_ENGINE_TOKEN':'t'*32}


def test_switch_reports_the_money_gateway_and_a_failed_start():
    assert any('activation is pending' in p for p in cloud_execution.problems(good()))
    e=good();e.pop(pg.CEILING_ENV)
    assert any('money gateway' in p for p in cloud_execution.problems(e))
    cloud_runtime._failure='RuntimeError: engine app could not start'
    assert any('engine app could not start' in p for p in cloud_execution.problems(good()))
    assert 'test-key' not in ' '.join(cloud_execution.problems({**good(),pg.KEY_ENV:''}))


def test_readiness_cannot_use_valid_prices_to_activate_removed_default():
    assert not cloud_execution.admitted(good())


def test_paid_selector_stays_fail_closed():
    with pytest.raises(ValueError,match='local only'):
        cloud_runtime.install(object(),gateway_env(DELIVERY_CHILD_EXECUTION='paid'),lambda c:pytest.fail('no activation'))
    assert not cloud_runtime.installed()
