"""Future-call expense evidence is calculated from frozen prices, not retail."""
from copy import deepcopy
import pytest
from delivery_api.call_metadata import frozen_expense
from delivery_api.provider_snapshot import ProviderSnapshot
from test_provider_snapshot import fixture


def quote():
    s=ProviderSnapshot(fixture())
    return {'provider':'deepseek','model':'fixture','expense_units_per_usd':10**12,
            'provider_evidence':s.quote_evidence('2026-10-10T11:00:00Z')}


USAGE={'input_tokens':12,'cache_hit_tokens':2,'cache_miss_tokens':10,'output_tokens':3}


def test_frozen_expense_preserves_cache_buckets_and_exact_decimal():
    q=quote();result=frozen_expense(q,USAGE,'2026-10-10T11:00:00Z','2026-10-10T11:00:01Z')
    assert result['expense_units']=='3306000'
    assert result['provider_expense_usd']=='0.000003306'
    assert result['status']=='calculated_frozen_rates' and result['band']=='off_peak'
    assert 'not provider invoice confirmation' in result['basis']
    before=deepcopy(result)
    q['provider_evidence']['snapshot']['published_usd_per_million']['off_peak']['output']='999'
    assert result==before


def test_cross_band_uses_existing_conservative_rule_not_current_rate():
    result=frozen_expense(quote(),USAGE,'2026-10-12T00:59:59Z','2026-10-12T01:00:01Z')
    assert result['band']=='peak' and result['expense_units']=='6612000'


@pytest.mark.parametrize('change',[{'expense_units_per_usd':10**9},{'expense_units_per_usd':True},
    {'model':'other'},{'provider_evidence':None}])
def test_unknown_or_conflicting_price_facts_are_not_zero(change):
    with pytest.raises((ValueError,KeyError)):
        frozen_expense(quote()|change,USAGE,'2026-10-10T11:00:00Z','2026-10-10T11:00:01Z')


@pytest.mark.parametrize('change',[{'cache_hit_tokens':True},{'cache_miss_tokens':11},{'output_tokens':-1}])
def test_unmeasured_or_inconsistent_usage_cannot_be_priced(change):
    with pytest.raises(ValueError):
        frozen_expense(quote(),USAGE|change,'2026-10-10T11:00:00Z','2026-10-10T11:00:01Z')


def test_call_facts_keep_exact_identity_and_unknown_expense_without_a_quote():
    from datetime import datetime,timezone
    from delivery_api.call_metadata import call_evidence
    from delivery_api.child_provider_broker import ProviderCall
    from test_child_provider_broker import request
    call=ProviderCall.model_validate(request()|{'metadata':{'item':'scenario:1','fields':['trigger']}})
    start=datetime(2026,10,10,11,tzinfo=timezone.utc)
    result=call_evidence(call,USAGE,None,start,start,0.25)
    assert result['logical_call_id']=='call-1' and result['fields']==['trigger']
    assert result['provider']=='deepseek' and result['model']=='deepseek-flash'
    assert result['provider_expense']=={'status':'unknown_missing_frozen_quote'}
    assert result['cache_hit_tokens']==2 and result['cache_miss_tokens']==10
    # A pricing mismatch stays unknown, never turns into zero or stops usage.
    result=call_evidence(call,USAGE,quote(),start,start,0.25)
    assert result['provider_expense']=={'status':'unknown_invalid_frozen_evidence'}


@pytest.mark.parametrize('metadata',[{'item':'private?token=123'}, {'fields':['raw prompt']},
    {'fields':['x']*33},{'item':'x'*201},{'endpoint':'https://external.invalid'}])
def test_call_metadata_is_typed_bounded_and_not_an_effect_channel(metadata):
    from delivery_api.child_provider_broker import ProviderCall
    from test_child_provider_broker import request
    with pytest.raises(ValueError):ProviderCall.model_validate(request()|{'metadata':metadata})


@pytest.mark.parametrize('ready',[True,False])
def test_local_legacy_readiness_is_catalog_only_and_fails_before_dispatch(monkeypatch,ready):
    from delivery_api.call_metadata import check_installed_schema
    from contextlib import contextmanager
    seen=[]
    class Connection:
        def execute(self,sql):seen.append(sql);return self
        def fetchone(self):return {'ready':ready}
    class Pool:
        @contextmanager
        def connection(self):yield Connection()
    monkeypatch.setattr('delivery_api.db_pool.pool_for',lambda dsn:Pool())
    if ready:check_installed_schema('fixture')
    else:
        with pytest.raises(ValueError,match='migration missing'):check_installed_schema('fixture')
    assert seen==['SET TRANSACTION READ ONLY',"SELECT to_regprocedure('public.requi_call_evidence_version()') IS NOT NULL AS ready"]
