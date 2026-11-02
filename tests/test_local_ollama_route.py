"""The operator's local Ollama route: env-gated managed route, $0 reviewed gateway, strict usage, fixed origin.

Hermetic: no network, no database, no provider. Deployed-cloud isolation is proven by
running every check with the local-preview flag absent (the deployed server env).
"""
import asyncio
import json

import httpx
import pytest

from delivery_api import config, local_route
from delivery_api import production_gateway as pg
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.ollama_usage import normalize, settlement_usage

LOCAL_MODEL = 'qwen2.5-coder:14b'


def local_row(**over):
    base = {'provider': 'ollama', 'model': LOCAL_MODEL, 'price_version': 'local-t1', 'source': 'test-local',
            'expense_input': 0, 'expense_output': 0, 'retail_input': 0, 'retail_output': 0,
            'retail_units_per_credit': 10**9}
    base.update(over)
    return base


LOCAL_CEILING = {'model': LOCAL_MODEL, 'context_tokens': 32768, 'maximum_output_tokens': 12288,
                 'source': 'test-local', 'bound_version': 'local-t1', 'protective_run_ceiling': False}


def local_env(**over):
    base = {pg.PRICES_ENV: json.dumps([local_row()]), pg.CEILING_ENV: json.dumps(LOCAL_CEILING),
            'DELIVERY_LOCAL_PREVIEW': '1'}
    base.update(over)
    return base


# --- the managed route list -------------------------------------------------

def test_deployed_cloud_manages_exactly_the_deepseek_route():
    assert local_route.managed_routes({}) == ['deepseek']
    assert local_route.managed_routes({'DELIVERY_LOCAL_PREVIEW': '0'}) == ['deepseek']
    assert local_route.managed_routes({'DELIVERY_LOCAL_PREVIEW': 'true'}) == ['deepseek']


def test_local_preview_adds_exactly_one_managed_ollama_route():
    assert local_route.managed_routes({'DELIVERY_LOCAL_PREVIEW': '1'}) == ['deepseek', 'ollama']


# --- the $0 reviewed gateway -------------------------------------------------

def test_local_gateway_builds_with_zero_prices_and_no_vendor_parts():
    factory = pg.gateways_from_environment(object(), local_env())
    try:
        assert factory.local is True and factory.model == LOCAL_MODEL
        assert factory.retail_rates is None and factory.verify_vendor is None and factory.provider_balance is None
        gateway = factory(None, 'job-1')
        assert gateway.require_prepaid is True and gateway.retail_rates is None
        assert gateway.resolve_serialized is not None and gateway.verify_provider_snapshot is None
        assert gateway.provider_balance is None
    finally:
        factory.close()


def test_local_quote_is_complete_exact_and_zero_priced():
    factory = pg.gateways_from_environment(object(), local_env())
    try:
        quote, digest = factory.catalog.resolve_serialized(
            stage='needs', provider='ollama', model=LOCAL_MODEL,
            serialized_request=json.dumps({'model': LOCAL_MODEL, 'max_tokens': 2048}).encode(),
            max_output_tokens=2048)
        assert quote['provider'] == 'ollama' and quote['model'] == LOCAL_MODEL
        assert quote['expense_input'] == quote['expense_output'] == 0
        assert quote['retail_input'] == quote['retail_output'] == 0
        assert 'estimate_policy' not in quote and 'provider_evidence' not in quote and 'retail_formula' not in quote
        assert len(digest) == 64
        pg.validate_local_launch_quote(quote)
    finally:
        factory.close()


def test_local_gateway_refused_without_the_preview_flag():
    env = local_env()
    env.pop('DELIVERY_LOCAL_PREVIEW')
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), env)
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(DELIVERY_LOCAL_PREVIEW='0'))


def test_local_gateway_refused_with_a_provider_key():
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(DEEPSEEK_API_KEY='sk-test'))


@pytest.mark.parametrize('field', ['expense_input', 'expense_output', 'retail_input', 'retail_output'])
def test_local_gateway_refused_with_a_nonzero_price(field):
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.PRICES_ENV: json.dumps([local_row(**{field: 1})])}))


def test_local_gateway_refused_with_cache_retail_or_vendor_parts():
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.PRICES_ENV: json.dumps([local_row(expense_cache_hit=0)])}))
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.PRICES_ENV: json.dumps([local_row(retail_formula={'x': 1})])}))
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.PRICES_ENV: json.dumps([local_row(provider_snapshot={'x': 1})])}))


def test_local_gateway_refused_with_estimate_style_ceiling_or_model_mismatch():
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.CEILING_ENV: json.dumps({**LOCAL_CEILING, 'protective_run_ceiling': True})}))
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), local_env(**{pg.CEILING_ENV: json.dumps({**LOCAL_CEILING, 'model': 'other'})}))


def test_a_doctored_local_launch_quote_is_refused():
    factory = pg.gateways_from_environment(object(), local_env())
    try:
        quote, _ = factory.catalog.resolve_serialized(
            stage='needs', provider='ollama', model=LOCAL_MODEL,
            serialized_request=json.dumps({'model': LOCAL_MODEL, 'max_tokens': 1}).encode(), max_output_tokens=1)
    finally:
        factory.close()
    with pytest.raises(AdmissionDenied):
        pg.validate_local_launch_quote({**quote, 'expense_input': 5})
    with pytest.raises(AdmissionDenied):
        pg.validate_local_launch_quote({**quote, 'estimate_policy': {'kind': 'complete-wire-bytes-plus4096-v1', 'ceiling': 99999,
                                                                     'input_estimate': quote['input_max'], 'hard_output_cap': 1,
                                                                     'verified_input_bound': False}})


def test_deepseek_gateway_validation_is_unchanged():
    # Regression: the managed DeepSeek branch keeps its exact requirements (no silent rewrite).
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('gateway_test_inputs', pathlib.Path(__file__).with_name('test_production_gateway.py'))
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    factory = pg.gateways_from_environment(object(), inputs.env())
    try:
        assert factory.local is False and factory.retail_rates is not None
        assert factory.verify_vendor is not None and factory.provider_balance is not None
    finally:
        factory.close()
    with pytest.raises(AdmissionDenied):
        pg.gateways_from_environment(object(), inputs.env(**{inputs.pg.KEY_ENV: ''}))


# --- strict local usage ------------------------------------------------------

def test_local_usage_accepts_the_openai_compatible_shape():
    raw = {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}
    assert settlement_usage(raw) == {'input_tokens': 5, 'output_tokens': 2}
    assert normalize(raw)['raw_usage'] == raw


@pytest.mark.parametrize('raw', [
    {'completion_tokens': 2, 'total_tokens': 7},                                  # missing prompt
    {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 8},              # inconsistent total
    {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7, 'prompt_cache_hit_tokens': 1},  # no cache billing locally
    {'prompt_tokens': '5', 'completion_tokens': 2, 'total_tokens': 7},            # non-integer
    'not-a-dict',
])
def test_local_usage_refuses_unknown_or_inconsistent_shapes(raw):
    with pytest.raises(AdmissionDenied):
        settlement_usage(raw)


# --- fixed local origin -------------------------------------------------------

@pytest.mark.producer_integration  # factory imports the owner engine's provider_transport (cross-repo); full local release check covers it
@pytest.mark.engine_integration  # the owner_module seam imports interview_engine.provider_transport
def test_local_clients_are_pinned_to_the_configured_origin(monkeypatch):
    from delivery_api.provider_network import ollama_client_factory
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1')
    seen = []
    def inner():
        return httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, json={}))
    factory = ollama_client_factory(inner_sync=inner, inner_async=lambda: None, owner_module='interview_engine')
    with pytest.raises(ValueError):
        factory(stage='needs', provider='deepseek', model='x', gateway=None)
    sync, _ = factory(stage='needs', provider='ollama', model=LOCAL_MODEL, gateway=None)
    try:
        fixed = sync._transport.inner  # FixedOriginTransport under the admitted wrapper
        response = fixed.handle_request(httpx.Request('POST', 'http://localhost:11434/v1/chat/completions', content=b'{}'))
        assert response.status_code == 200 and len(seen) == 1
        for bad in (httpx.Request('GET', 'http://localhost:11434/v1/chat/completions'),
                    httpx.Request('POST', 'http://localhost:11434/api/show'),
                    httpx.Request('POST', 'http://other-host:11434/v1/chat/completions'),
                    httpx.Request('POST', 'https://localhost:11434/v1/chat/completions')):
            with pytest.raises(ValueError):
                fixed.handle_request(bad)
        assert len(seen) == 1
    finally:
        sync.close()


# --- the execution switch ------------------------------------------------------

def test_execution_switch_admits_the_local_route_only_with_the_flag(monkeypatch):
    from delivery_api import cloud_execution
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    monkeypatch.delenv('DELIVERY_CONTROL_PLANE_ONLY', raising=False)
    good = local_env(DELIVERY_CLOUD_EXECUTION='1', INTERVIEW_ENGINE_TOKEN='t' * 32, DELIVERY_CHILD_EXECUTION='local')
    assert cloud_execution.problems(good) == []
    noflag = dict(good); noflag.pop('DELIVERY_LOCAL_PREVIEW')
    assert any('explicit local preview' in p for p in cloud_execution.problems(noflag))
    assert any('no provider key' in p for p in cloud_execution.problems({**good, 'DEEPSEEK_API_KEY': 'sk-test'}))


# --- the deployed boundary (app) ----------------------------------------------

def _cloud_app(monkeypatch, flag):
    from delivery_api import app as module
    monkeypatch.setattr(config, 'CLOUD_TENANTS', True)
    if flag:
        monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW', '1')
    else:
        monkeypatch.delenv('DELIVERY_LOCAL_PREVIEW', raising=False)
    monkeypatch.setattr(module, 'cloud_execution_admitted', lambda: True)
    from types import SimpleNamespace
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={'ollama':LOCAL_MODEL} if flag else {'deepseek':'deepseek-flash'}))
    return module


def test_deployed_route_list_displays_both_but_only_deepseek_is_admitted(monkeypatch):
    module = _cloud_app(monkeypatch, flag=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-only')
    def preview(route):
        assert route=='deepseek', 'disabled Ollama must not be probed'
        return {'model_map':{'probing':['deepseek','deepseek-flash']}}
    monkeypatch.setattr(module.engine_client,'preview_route',preview)
    result=module.model_routes(a=module.auth.Actor('test-owner','Test owner'))
    assert [r['id'] for r in result['routes']]==['deepseek','ollama']
    assert result['routes'][0]['available'] is True
    assert result['routes'][1]['available'] is False
    assert 'DELIVERY_LOCAL_PREVIEW' in result['routes'][1]['error']
    assert 'deepseek only' in result['routes'][1]['error']
    assert result['default_route']=={'route':'deepseek'}


def test_local_preview_route_list_adds_managed_ollama(monkeypatch):
    module = _cloud_app(monkeypatch, flag=True)
    monkeypatch.setattr(module.engine_client, 'preview_route', lambda route: {
        'model_map': {stage: ['ollama', LOCAL_MODEL] for stage in ('probing', 'distillation')}})
    result = module.model_routes(a=module.auth.Actor('test-owner','Test owner'))
    assert [r['id'] for r in result['routes']] == ['deepseek', 'ollama']
    ollama = result['routes'][1]
    assert ollama['managed'] is True and ollama['available'] is True and ollama['model'] == 'ollama:' + LOCAL_MODEL
    assert result['default_route'] == {'route': 'ollama'}


def test_a_keyless_deepseek_route_shows_unavailable_missing_key(monkeypatch):
    """2026-10-27: availability was open_now alone, so an ollama-mode preview
    (no key ever reaches the container) offered a selectable DeepSeek route
    that failed at start. A dead route must say so, naming the missing key -
    never its value."""
    module = _cloud_app(monkeypatch, flag=True)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(module.engine_client, 'preview_route', lambda route: {
        'model_map': {stage: ['ollama', LOCAL_MODEL] for stage in ('probing', 'distillation')}})
    result = module.model_routes(a=module.auth.Actor('test-owner','Test owner'))
    deepseek = result['routes'][0]
    assert deepseek['available'] is False
    assert 'missing key' in deepseek['error'] and 'DEEPSEEK_API_KEY' in deepseek['error']
    assert result['routes'][1]['available'] is True, 'the local route is untouched'


def test_a_present_key_cannot_bypass_the_ollama_only_gateway(monkeypatch):
    module = _cloud_app(monkeypatch, flag=True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'sk-test-0123456789abcdef')
    monkeypatch.setattr(module.engine_client, 'preview_route', lambda route: {
        'model_map': {stage: ['ollama', LOCAL_MODEL] for stage in ('probing', 'distillation')}})
    result = module.model_routes(a=module.auth.Actor('test-owner','Test owner'))
    deepseek = result['routes'][0]
    assert deepseek['available'] is False and 'ollama only' in deepseek['error']
    assert result['default_route']=={'route':'ollama'}


def test_local_preview_route_list_fails_closed_when_the_engine_cannot_resolve(monkeypatch):
    module = _cloud_app(monkeypatch, flag=True)
    def down(route):
        raise module.EngineUnreachableError('http://engine', RuntimeError('down'))
    monkeypatch.setattr(module.engine_client, 'preview_route', down)
    result = module.model_routes(a=module.auth.Actor('test-owner','Test owner'))
    assert result['routes'][1]['available'] is False and 'error' in result['routes'][1]


def test_deployed_start_rejects_a_forced_ollama_route(monkeypatch, pg_scoped_workspace):
    module = _cloud_app(monkeypatch, flag=False)
    store = module.store
    task = store.create_task('forced-ollama-start-' + LOCAL_MODEL, 'an idea')
    task_id = task['task_id'] if isinstance(task, dict) else task
    def explode(*args, **kwargs):
        raise AssertionError('a rejected route must not reach the engine')
    monkeypatch.setattr(module.engine_client, 'preview_route', explode)
    monkeypatch.setattr(module.engine_client, 'default_route', explode)
    from delivery_api import auth
    actor = auth.Actor(id='owner-1', label='tester')
    with pytest.raises(module.HTTPException) as raised:
        asyncio.run(module.start_task_engine(task_id, module.TaskStartRequest(model_route='ollama'), a=actor))
    assert raised.value.status_code == 422
    store.delete_task(task_id)


def test_deployed_start_rejects_a_forced_ollama_route_even_when_open(monkeypatch, pg_scoped_workspace):
    # The rejection is route policy, not execution state: it fires while execution is admitted.
    test_deployed_start_rejects_a_forced_ollama_route(monkeypatch, pg_scoped_workspace)


def test_engine_model_must_match_the_installed_gateway(monkeypatch):
    module=_cloud_app(monkeypatch,flag=True)
    monkeypatch.setattr(module.engine_client,'preview_route',lambda route:{'model_map':{'probing':['ollama','unreviewed-model']}})
    result=module.model_routes(module.auth.Actor('test-owner','Test owner'))
    assert result['routes'][1]['available'] is False
    assert 'reviewed model' in result['routes'][1]['error']
    assert result['default_route'] is None


def test_missing_gateway_disables_both_without_probing(monkeypatch):
    module=_cloud_app(monkeypatch,flag=True)
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:None)
    monkeypatch.setattr(module.engine_client,'preview_route',lambda route:(_ for _ in ()).throw(AssertionError('must not probe')))
    result=module.model_routes(module.auth.Actor('test-owner','Test owner'))
    assert all(not r['available'] and 'engine did not install after readiness checks' in r['error'] for r in result['routes'])
    assert result['default_route'] is None


def test_engine_model_missing_reason_is_preserved_below_disabled_route(monkeypatch):
    module=_cloud_app(monkeypatch,flag=True)
    from vmodel_contracts.interactive import ProtocolError,ProtocolErrorCode
    def missing(route):
        raise module.EngineClientError(422,ProtocolError(code=ProtocolErrorCode.INVALID_RESPONSE,detail='Ollama model phi4 is unavailable; pull phi4 and retry'))
    monkeypatch.setattr(module.engine_client,'preview_route',missing)
    result=module.model_routes(module.auth.Actor('test-owner','Test owner'))
    assert result['routes'][1]['available'] is False
    assert result['routes'][1]['error']=='Ollama model phi4 is unavailable; pull phi4 and retry'
    assert result['default_route'] is None


def test_dual_preview_offers_both_only_after_independent_engine_preflights(monkeypatch):
    module=_cloud_app(monkeypatch,flag=True)
    from types import SimpleNamespace
    from delivery_api import cloud_runtime
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setattr(cloud_runtime,'gateway_factory',lambda:SimpleNamespace(models={'deepseek':'deepseek-flash','ollama':LOCAL_MODEL}))
    seen=[]
    def preview(route):
        seen.append(route)
        return {'model_map':{'probing':[route,LOCAL_MODEL if route=='ollama' else 'deepseek-flash']}}
    monkeypatch.setattr(module.engine_client,'preview_route',preview)
    result=module.model_routes(module.auth.Actor('test-owner','Test owner'))
    assert seen==['deepseek','ollama']
    assert all(route['available'] for route in result['routes'])
    def missing_local(route):
        if route=='ollama':raise module.EngineUnreachableError('local',RuntimeError('down'))
        return preview(route)
    monkeypatch.setattr(module.engine_client,'preview_route',missing_local)
    result=module.model_routes(module.auth.Actor('test-owner','Test owner'))
    assert result['routes'][0]['available'] and not result['routes'][1]['available']
