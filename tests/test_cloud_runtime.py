"""Hermetic checks of cloud runtime start-up. Fakes only; no network, no provider, no database."""
import sys,types
import pytest
from delivery_api import cloud_runtime,cloud_execution,config,inprocess_engine,inprocess_interview,production_gateway as pg
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


@pytest.fixture
def fakes(monkeypatch):
    runtime=types.ModuleType('vmodel_contracts.runtime');runtime.artifacts_root=lambda value=None:__import__('pathlib').Path(value or '/tmp/art')
    pkg=types.ModuleType('vmodel_contracts');pkg.__path__=[]
    monkeypatch.setitem(sys.modules,'vmodel_contracts',pkg);monkeypatch.setitem(sys.modules,'vmodel_contracts.runtime',runtime)
    made={}
    class Host:
        def __init__(self,store,base,**kw):made['host']=self;self.kw=kw;self.base=base
        def client(self):
            if made.get('fail'):raise RuntimeError('engine app could not start')
            return 'hosted-client'
    monkeypatch.setattr(inprocess_interview,'InProcessInterviewHost',Host)
    monkeypatch.setattr(cloud_runtime,'_clients',lambda module:'clients:'+module)
    return made


def test_check_gateways_accepts_a_full_reviewed_environment_and_refuses_a_partial_one():
    cloud_runtime.check_gateways(gateway_env())
    e=gateway_env();e.pop(pg.CEILING_ENV)
    with pytest.raises(AdmissionDenied):cloud_runtime.check_gateways(e)


def test_install_wires_engine_host_and_client_once(fakes):
    chosen=[]
    cloud_runtime.install(object(),gateway_env(VMODEL_ARTIFACTS_DIR='/tmp/a'),chosen.append)
    assert cloud_runtime.installed() and chosen==['hosted-client'] and cloud_runtime.failure() is None
    assert inprocess_engine.current() is not None and inprocess_interview.current() is fakes['host']
    assert fakes['host'].kw['generator_clients']=='clients:spec_generator' and fakes['host'].kw['interview_clients']=='clients:interview_engine'
    assert str(fakes['host'].base)=='/tmp/a'
    cloud_runtime.install(object(),gateway_env(),chosen.append)
    assert chosen==['hosted-client']


def test_uninstall_removes_everything(fakes):
    cloud_runtime.install(object(),gateway_env(),lambda c:None);cloud_runtime.uninstall()
    assert not cloud_runtime.installed() and inprocess_engine.current() is None and inprocess_interview.current() is None


def test_a_failed_start_installs_nothing_and_keeps_the_reason(fakes):
    fakes['fail']=True;chosen=[]
    with pytest.raises(RuntimeError):cloud_runtime.install(object(),gateway_env(),chosen.append)
    assert chosen==[] and not cloud_runtime.installed()
    assert inprocess_engine.current() is None and inprocess_interview.current() is None
    assert 'engine app could not start' in cloud_runtime.failure()


def test_bad_gateway_configuration_installs_nothing(fakes):
    e=gateway_env();e.pop(pg.KEY_ENV)
    with pytest.raises(AdmissionDenied):cloud_runtime.install(object(),e,lambda c:None)
    assert not cloud_runtime.installed() and inprocess_engine.current() is None


def good():
    return {**gateway_env(),'DELIVERY_CLOUD_EXECUTION':'1','INTERVIEW_ENGINE_TOKEN':'t'*32}


def test_switch_reports_the_money_gateway_and_a_failed_start():
    assert cloud_execution.problems(good())==[]
    e=good();e.pop(pg.CEILING_ENV)
    assert any('money gateway' in p for p in cloud_execution.problems(e))
    cloud_runtime._failure='RuntimeError: engine app could not start'
    assert any('engine app could not start' in p for p in cloud_execution.problems(good()))
    assert 'test-key' not in ' '.join(cloud_execution.problems({**good(),pg.KEY_ENV:''}))


def test_a_configuration_that_built_once_is_not_rebuilt(monkeypatch):
    calls=[]
    real=pg.ProductionGateways
    class Counting(real):
        def __init__(self,*a,**k):calls.append(1);super().__init__(*a,**k)
    monkeypatch.setattr(pg,'ProductionGateways',Counting)
    same=gateway_env()
    for _ in range(3):cloud_runtime.check_gateways(same)
    assert len(calls)==1
    e=gateway_env();e.pop(pg.KEY_ENV)
    with pytest.raises(AdmissionDenied):cloud_runtime.check_gateways(e)
    with pytest.raises(AdmissionDenied):cloud_runtime.check_gateways(e)
    assert len(calls)==3
