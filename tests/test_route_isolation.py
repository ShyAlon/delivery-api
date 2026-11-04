"""Route readiness stays independent; no provider/network/database used."""
import importlib.util,json,pathlib
from types import SimpleNamespace
import pytest
from delivery_api import model_pins as pins,production_gateway as pg,cloud_runtime
spec=importlib.util.spec_from_file_location('local_inputs',pathlib.Path(__file__).with_name('test_local_ollama_route.py'))
local=importlib.util.module_from_spec(spec);spec.loader.exec_module(local)


def setup(monkeypatch,**over):
 env={**local.local_env(),'REQUI_PIN_STORE':'1','REQUI_PIN_PROVIDERS':'deepseek,ollama','DELIVERY_CHILD_EXECUTION':'local','DELIVERY_DATABASE_URL':'fixture',**over}
 payload={'row':json.loads(env[pg.PRICES_ENV])[0],'context':json.loads(env[pg.CEILING_ENV])}
 monkeypatch.setattr(pins.PinRepository,'list',lambda self:[{'provider':'ollama','is_default':True,'payload':payload}])
 return env,payload


def test_missing_paid_default_does_not_hide_valid_zero_local_route(monkeypatch):
 env,payload=setup(monkeypatch,DEEPSEEK_API_KEY='paid-key-not-for-local')
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert gateway.models=={'ollama':payload['row']['model']}
 assert gateway.route_errors.keys()=={'deepseek'}
 factory=gateway._factory(pins.environment([payload]))
 assert factory.local and factory.verify_vendor is None and factory.provider_balance is None
 quote,_=factory.catalog.resolve_serialized(stage='preflight',provider='ollama',model=payload['row']['model'],serialized_request=json.dumps({'model':payload['row']['model'],'max_tokens':1}).encode(),max_output_tokens=1)
 assert all(quote[k]==0 for k in ('expense_input','expense_output','retail_input','retail_output'))
 gateway.close()


def test_local_model_and_bounds_remain_required(monkeypatch):
 env,payload=setup(monkeypatch)
 payload['context']['model']='wrong-model'
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert gateway.models=={}
 assert 'ollama' in gateway.route_errors
 with pytest.raises(ValueError,match='No ready reviewed route'):gateway.available_environment()


def test_local_pin_cannot_acquire_a_nonzero_price(monkeypatch):
 env,payload=setup(monkeypatch);payload['row']['retail_input']=1
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert gateway.models=={} and 'ollama' in gateway.route_errors


def test_task_generation_captures_only_valid_independent_routes(monkeypatch):
 env,payload=setup(monkeypatch);saved={}
 store=SimpleNamespace(_postgres=object(),task=lambda t:{'generation':4},decision_review=lambda *a:saved.get('pin'),retain_decision_review=lambda *a:saved.update(pin=a[-1]))
 gateway=pins.DurableGateways(store,env)
 assert gateway.for_task('task').models=={'ollama':payload['row']['model']}
 payload['context']['model']='later-invalid'
 assert gateway.for_task('task').models=={'ollama':json.loads(saved['pin'][pg.PRICES_ENV])[0]['model']}


def test_gateway_resolution_uses_the_same_independent_routes(monkeypatch):
 env,_=setup(monkeypatch)
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert gateway.models.keys()=={'ollama'}


def test_failed_paid_refresh_is_scoped_and_never_contacts_local_vendor(monkeypatch):
 env,payload=setup(monkeypatch,DELIVERY_CHILD_EXECUTION='attended-paid',DEEPSEEK_API_KEY='fixture-paid')
 rows=[{'provider':'deepseek','is_default':True,'payload':{'row':{'provider':'deepseek'}}},{'provider':'ollama','is_default':True,'payload':payload}]
 monkeypatch.setattr(pins.PinRepository,'list',lambda self:rows)
 calls=[]
 def failed(candidate):
  calls.append(candidate['row']['provider']);raise ValueError('stale paid sources')
 monkeypatch.setattr(pins,'refresh',failed)
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 with pytest.warns(UserWarning,match='deepseek route unavailable'):gateway.refresh_launch()
 assert calls==['deepseek'] and gateway.models=={'ollama':payload['row']['model']}
 assert 'stale paid sources' in gateway.route_errors['deepseek']


def test_missing_local_pin_does_not_implicitly_create_model_or_bounds(monkeypatch):
 env,_=setup(monkeypatch)
 monkeypatch.setattr(pins.PinRepository,'list',lambda self:[])
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert gateway.models=={} and set(gateway.route_errors)=={'deepseek','ollama'}


def test_paid_preview_does_not_create_paid_authority_for_local_only_ready_set(monkeypatch):
 from delivery_api import provider_runtime
 env,payload=setup(monkeypatch,DELIVERY_CHILD_EXECUTION='attended-paid',DELIVERY_PAID_CHILD_PREVIEW='1',DELIVERY_PREVIEW='1',DELIVERY_PUBLIC_ORIGIN='http://127.0.0.1:8770')
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 assert provider_runtime.construct(env,gateway) is None
 env['DELIVERY_PUBLIC_ORIGIN']='https://deployed.example'
 assert provider_runtime.construct(env,gateway) is None


def test_output_cap_and_model_mismatch_are_still_refused(monkeypatch):
 env,payload=setup(monkeypatch)
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 factory=gateway._factory(gateway.available_environment())
 for model,cap in [('wrong-model',1),(payload['row']['model'],payload['context']['maximum_output_tokens']+1)]:
  with pytest.raises(pg.AdmissionDenied):factory.catalog.resolve_serialized(stage='preflight',provider='ollama',model=model,serialized_request=json.dumps({'model':model,'max_tokens':cap}).encode(),max_output_tokens=cap)


def test_installer_boots_local_when_paid_review_is_missing(monkeypatch,tmp_path):
 env,payload=setup(monkeypatch,DELIVERY_CHILD_EXECUTION='attended-paid',DELIVERY_PAID_CHILD_PREVIEW='1',DELIVERY_PREVIEW='1',DELIVERY_PUBLIC_ORIGIN='http://127.0.0.1:8770',DELIVERY_DATABASE_URL='host=fixture dbname=fixture',VMODEL_ARTIFACTS_DIR=str(tmp_path))
 from delivery_api import child_generator,child_interview,inprocess_engine
 class Engine:
  def __init__(self,*a,**kw):self.paid_runtime=kw['paid_runtime']
 class Host(Engine):
  def client(self):return self
  def close(self):pass
 monkeypatch.setattr(child_generator,'ChildGeneratorEngine',Engine)
 monkeypatch.setattr(child_interview,'ChildInterviewHost',Host)
 chosen=[];cloud_runtime.uninstall()
 try:
  cloud_runtime.install(SimpleNamespace(_postgres=object()),env,chosen.append)
  assert cloud_runtime.installed() and chosen[0].paid_runtime is None
  assert cloud_runtime.gateway_factory().models=={'ollama':payload['row']['model']}
 finally:cloud_runtime.uninstall();cloud_runtime._failure=None


def test_no_paid_key_never_refreshes_paid_sources_before_local_start(monkeypatch):
 env,payload=setup(monkeypatch,DELIVERY_CHILD_EXECUTION='attended-paid')
 rows=[{'provider':'deepseek','is_default':True,'payload':{'row':{'provider':'deepseek'}}},{'provider':'ollama','is_default':True,'payload':payload}]
 monkeypatch.setattr(pins.PinRepository,'list',lambda self:rows)
 monkeypatch.setattr(pins,'refresh',lambda value:pytest.fail('No paid key: no paid source or balance request is needed'))
 gateway=pins.DurableGateways(SimpleNamespace(_postgres=object()),env)
 with pytest.warns(UserWarning,match='deepseek route unavailable'):gateway.refresh_launch()
 assert gateway.models=={'ollama':payload['row']['model']}
 assert 'key' in gateway.route_errors['deepseek']
