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
        assert gateway.require_prepaid is True and gateway.retail_rates is not None
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
