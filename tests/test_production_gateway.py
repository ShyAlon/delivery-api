"""Hermetic checks of the production gateway factory. No network, no database, no provider."""
import datetime
import json

import httpx
import pytest

from delivery_api import producer_admission,production_gateway as pg,provider_snapshot
from delivery_api.attempt_ledger import AdmissionDenied

MODEL='deepseek-test-model'
RATES={'cache_hit':'0.01','cache_miss':'0.10','output':'0.20'}


def snapshot():
    return {'model':MODEL,'model_version':'v1','parser_version':'deepseek-html-v2','published_period_rule':provider_snapshot.RULE,
        'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'sources':[{'url':'https://api-docs.deepseek.com/quick_start/pricing/','sha256':'a'*64},{'url':'https://api-docs.deepseek.com/api/create-chat-completion/','sha256':'b'*64}],
        'published_usd_per_million':{'peak':dict(RATES),'off_peak':dict(RATES)}}


def row(**over):
    base={'provider':'deepseek','model':MODEL,'price_version':'t1','source':'test','expense_input':100000,'expense_output':200000,'retail_input':12500,'retail_output':12500,
        'usage_categories':['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens'],'expense_cache_hit':10000,'expense_cache_miss':100000,
        'expense_units_per_usd':10**12,'retail_units_per_credit':10**9,
        'retail_formula':{'formula_version':'tokens-plus-compute-v1','units_per_credit':10**9,'price_per_token':'0.0000125','price_per_10ms':'0.0000005','usd_per_credit':'1.00'},
        'provider_snapshot':snapshot()}
    base.update(over)
    return base


CEILING={'model':MODEL,'context_tokens':128000,'maximum_output_tokens':8000,'source':'test','bound_version':'t1','protective_run_ceiling':True}


def env(**over):
    base={pg.PRICES_ENV:json.dumps([row()]),pg.CEILING_ENV:json.dumps(CEILING),pg.KEY_ENV:'test-key'}
    base.update(over)
    return base


def test_builds_with_every_reviewed_input_and_makes_a_job_bound_gateway():
    factory=pg.gateways_from_environment(object(),env())
    try:
        gateway=factory(None,'job-1')
        assert factory.model==MODEL and gateway.job_id=='job-1'
        assert gateway.launch_guard is True and gateway.require_prepaid is True and gateway.retail_rates is not None
        assert gateway.resolve_serialized is not None and gateway.verify_provider_snapshot is not None and gateway.provider_balance is not None
    finally:factory.close()


@pytest.mark.parametrize('name',[pg.PRICES_ENV,pg.CEILING_ENV,pg.KEY_ENV])
def test_missing_input_means_no_gateway(name):
    e=env();e.pop(name)
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),e)


def test_whitespace_key_and_bad_json_refused():
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.KEY_ENV:'  '}))
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.PRICES_ENV:'{not json'}))


@pytest.mark.parametrize('bad',[
    dict(retail_units_per_credit=10**6),
    dict(provider='other'),
    dict(retail_formula={'formula_version':'x'}),
])
def test_unreviewed_price_row_refused(bad):
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.PRICES_ENV:json.dumps([row(**bad)])}))


def test_row_without_formula_or_snapshot_refused():
    for key in ('retail_formula','provider_snapshot'):
        r=row();r.pop(key)
        with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.PRICES_ENV:json.dumps([r])}))


def test_exactly_one_price_row_required():
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.PRICES_ENV:json.dumps([row(),row(model='other')])}))
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.PRICES_ENV:'[]'}))


def test_ceiling_must_name_the_reviewed_model():
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.CEILING_ENV:json.dumps({**CEILING,'model':'other'})}))
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(object(),env(**{pg.CEILING_ENV:json.dumps({'model':MODEL})}))


def test_balance_reader_can_only_reach_the_fixed_account_endpoint():
    seen=[]
    def handler(request):
        seen.append((request.method,str(request.url)))
        return httpx.Response(200,json={'is_available':True})
    factory=pg.gateways_from_environment(object(),env(),balance_transport=httpx.MockTransport(handler))
    try:
        assert factory.provider_balance()['response']=={'is_available':True}
        assert seen==[('GET','https://api.deepseek.com/user/balance')]
        with pytest.raises(ValueError):factory.balance_client.get('https://api.deepseek.com/chat/completions')
        with pytest.raises(ValueError):factory.balance_client.get('https://evil.example/user/balance')
    finally:factory.close()


def test_dispatch_with_no_gateway_installed_refuses_before_any_provider_call():
    # The cloud runner asks for the job's gateway first; with none installed it must stop, not run unmetered.
    assert producer_admission._active_gateway.get() is None
    with pytest.raises(AdmissionDenied):producer_admission.current_worker_gateway(object(),'task-1')


def dual_env():
    local={'provider':'ollama','model':'phi4','price_version':'local-test','source':'test-local',
           'expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9}
    local_context={'provider':'ollama','model':'phi4','context_tokens':32768,'maximum_output_tokens':8000,
                   'source':'test-local','bound_version':'local-test','protective_run_ceiling':False}
    return env(**{pg.PRICES_ENV:json.dumps([row(),local]),pg.CEILING_ENV:json.dumps([dict(CEILING,provider='deepseek'),local_context]),'DELIVERY_LOCAL_PREVIEW':'1'})


def test_dual_gateway_binds_one_durable_route_per_job_without_cross_provider_money():
    class Store:
        jobs={'local':{'execution_options':{'model_route':'ollama:phi4'}},
              'paid':{'execution_options':{'model_route':'deepseek:'+MODEL}},
              'missing':{'execution_options':{}},
              'wrong':{'execution_options':{'model_route':'ollama:unknown'}}}
        def worker_job(self,j):return self.jobs[j]
    store=Store();contacts=[]
    def remote(request):contacts.append(request);raise AssertionError('must not contact provider while building/binding')
    factory=pg.gateways_from_environment(store,dual_env(),balance_transport=httpx.MockTransport(remote))
    try:
        assert factory.models=={'deepseek':MODEL,'ollama':'phi4'}
        local=factory(None,'local');paid=factory(None,'paid')
        assert local.retail_rates is None and local.verify_provider_snapshot is None and local.provider_balance is None
        assert paid.retail_rates is not None and paid.verify_provider_snapshot is not None and paid.provider_balance is not None
        assert local.measure_raw_usage({'prompt_tokens':3,'completion_tokens':2,'total_tokens':5})=={'input_tokens':3,'output_tokens':2}
        with pytest.raises(AdmissionDenied,match='durable.*selection'):factory(None,'missing')
        with pytest.raises(AdmissionDenied,match='reviewed'):factory(None,'wrong')
        import base64
        with pytest.raises(AdmissionDenied,match='phase selection'):
            local.reserve_serialized(attempt_id='x',stage='probing',provider='deepseek',model=MODEL,
                serialized_request=base64.b64encode(json.dumps({'model':MODEL,'max_tokens':1}).encode()).decode(),max_output_tokens=1)
        assert contacts==[]
    finally:factory.close()


@pytest.mark.parametrize('mutation',['duplicate','missing_ceiling','wrong_model','missing_key'])
def test_dual_gateway_refuses_incomplete_or_ambiguous_review(mutation):
    e=dual_env();rows=json.loads(e[pg.PRICES_ENV]);contexts=json.loads(e[pg.CEILING_ENV])
    if mutation=='duplicate':rows.append(rows[1])
    if mutation=='missing_ceiling':contexts.pop()
    if mutation=='wrong_model':contexts[1]['model']='wrong'
    if mutation=='missing_key':e.pop('DEEPSEEK_API_KEY')
    e[pg.PRICES_ENV]=json.dumps(rows);e[pg.CEILING_ENV]=json.dumps(contexts)
    with pytest.raises(AdmissionDenied):pg.gateways_from_environment(None,e)


def test_dual_settlement_refuses_foreign_quote_before_raw_evidence_or_normalization():
    class Store:
        def worker_job(self,j):return {'execution_options':{'model_route':'ollama:phi4'}}
        def provider_attempt(self,a):return {'job_id':'local','quote':{'provider':'deepseek','model':MODEL}}
        def record_raw_provider_usage(self,*a):raise AssertionError('wrong provider evidence must not be recorded')
    factory=pg.gateways_from_environment(Store(),dual_env())
    try:
        gateway=factory(None,'local')
        with pytest.raises(AdmissionDenied,match='quote.*selection'):
            gateway.settle_raw('foreign',{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5})
    finally:factory.close()

@pytest.mark.parametrize('origin',['http://localhost:8001','https://fixture.invalid'])
def test_dual_gateway_review_is_origin_independent(origin):
    e=dual_env();e.pop('DELIVERY_LOCAL_PREVIEW',None);e['DELIVERY_PUBLIC_ORIGIN']=origin
    gateway=pg.gateways_from_environment(None,e)
    assert set(gateway.models)=={'deepseek','ollama'}
