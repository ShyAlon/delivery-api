"""Hermetic checks of the cloud execution switch. No network, no provider."""
import json

import pytest

from delivery_api import cloud_execution,config

import importlib.util,pathlib
_spec=importlib.util.spec_from_file_location('gateway_test_inputs',pathlib.Path(__file__).with_name('test_production_gateway.py'))
_inputs=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_inputs)
GOOD={**_inputs.env(),'DELIVERY_CLOUD_EXECUTION':'1','INTERVIEW_ENGINE_TOKEN':'t'*32}
PRICES=GOOD['VMODEL_REVIEWED_MODEL_PRICES']


@pytest.fixture(autouse=True)
def cloud(monkeypatch):
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.delenv('DELIVERY_CONTROL_PLANE_ONLY',raising=False)


def test_closed_unless_flag_is_one():
    assert not cloud_execution.admitted({k:v for k,v in GOOD.items() if k!='DELIVERY_CLOUD_EXECUTION'})
    assert not cloud_execution.admitted({**GOOD,'DELIVERY_CLOUD_EXECUTION':'true'})


def test_open_only_with_every_check_passing():
    assert any('activation is pending' in p for p in cloud_execution.problems(GOOD))
    assert not cloud_execution.admitted(GOOD)
    for key,bad in (('DEEPSEEK_API_KEY',''),('INTERVIEW_ENGINE_TOKEN','local-interview-engine-token'),('INTERVIEW_ENGINE_TOKEN','short'),('VMODEL_REVIEWED_MODEL_PRICES','[]')):
        assert cloud_execution.problems({**GOOD,key:bad}),key


def test_closed_without_cloud_accounts_or_in_delivery_only_profile(monkeypatch):
    monkeypatch.setattr(config,'CLOUD_TENANTS',False)
    assert any('Supabase' in p for p in cloud_execution.problems(GOOD))
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    assert any('delivery-only' in p for p in cloud_execution.problems({**GOOD,'DELIVERY_CONTROL_PLANE_ONLY':'1'}))


def test_reasons_never_contain_secret_values():
    text=' '.join(cloud_execution.problems({**GOOD,'DEEPSEEK_API_KEY':'','INTERVIEW_ENGINE_TOKEN':'sekret-short'}))
    assert 'sekret-short' not in text


def test_readiness_uses_gateway_catalog_once_and_supplied_profile(monkeypatch):
    from delivery_api import production_gateway,cloud_runtime
    cloud_runtime._checked.clear();calls=[]
    original=production_gateway.catalog_from_environment
    def counted(*args,**kwargs):calls.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(production_gateway,'catalog_from_environment',counted)
    monkeypatch.setenv('DELIVERY_CONTROL_PLANE_ONLY','1')
    assert any('activation is pending' in p for p in cloud_execution.problems({**GOOD,'DELIVERY_CONTROL_PLANE_ONLY':'0'}))
    assert calls==[]  # activation boundary refuses before price preparation
