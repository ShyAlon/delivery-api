from types import SimpleNamespace
import pytest
from delivery_api.admission_catalog import ReviewedCatalog,ReviewedModelPrice,RequestBound
from delivery_api.attempt_ledger import AdmissionDenied

P=ReviewedModelPrice('fake','fake','TEST','test-source',1,2,3,4)
ARGS=dict(stage='needs',provider='fake',model='fake',messages=['test'],max_output_tokens=10)

def test_empty_catalog_or_missing_bounder_fails_closed():
    for catalog in (ReviewedCatalog(),ReviewedCatalog([P])):
        with pytest.raises(AdmissionDenied):catalog.resolve(**ARGS)

@pytest.mark.parametrize('bound',[
    RequestBound('TEST',1,10,b'test',False),
    RequestBound('TEST',1,9,b'test',True),
    RequestBound('',1,10,b'test',True),
    RequestBound('TEST',-1,10,b'test',True)])
def test_incomplete_or_inconsistent_bound_rejected(bound):
    catalog=ReviewedCatalog([P],{('fake','fake'):lambda **kw:bound})
    with pytest.raises(AdmissionDenied):catalog.resolve(**ARGS)

def test_test_only_exact_payload_bound_and_pinned_catalog():
    catalog=ReviewedCatalog([P],{('fake','fake'):lambda **kw:RequestBound('TEST',12,10,b'complete fake request including tools',True)})
    quote,digest=catalog.resolve(**ARGS)
    assert quote['price_version']=='TEST' and quote['input_max']==12 and len(digest)==64

@pytest.mark.parametrize('usage',[None,{}, {'input_tokens':1,'output_tokens':2,'total_tokens':9},
    {'input_tokens':1,'output_tokens':2,'cache_tokens':1}, {'input_tokens':True,'output_tokens':2}])
def test_unknown_or_conflicting_usage_has_no_fallback(usage):
    assert ReviewedCatalog.measure_standard_usage(SimpleNamespace(usage_metadata=usage)) is None

def test_standard_usage_is_explicit_and_consistent():
    assert ReviewedCatalog.measure_standard_usage(SimpleNamespace(usage_metadata={'input_tokens':1,'output_tokens':2,'total_tokens':3}))=={'input_tokens':1,'output_tokens':2}


def test_environment_catalog_has_no_defaults_and_preserves_quote_snapshot():
    import json
    from dataclasses import asdict
    from delivery_api.admission_catalog import catalog_from_environment
    env={'VMODEL_REVIEWED_MODEL_PRICES':json.dumps([asdict(P) | {}])}
    # usage_categories is not an env field; until adapters exist these remain standard only.
    row=asdict(P);row.pop('usage_categories')
    env['VMODEL_REVIEWED_MODEL_PRICES']=json.dumps([row])
    bounders={('fake','fake'):lambda **kw:RequestBound('TEST',12,10,b'complete fake',True)}
    first=catalog_from_environment(env,bounders=bounders)
    quote,_=first.resolve(**ARGS)
    row['expense_input']=99
    env['VMODEL_REVIEWED_MODEL_PRICES']=json.dumps([row])
    second=catalog_from_environment(env,bounders=bounders)
    assert second.resolve(**ARGS)[0]['expense_input']==99
    assert quote['expense_input']==1 and first.resolve(**ARGS)[0]['expense_input']==1
    with pytest.raises(AdmissionDenied):catalog_from_environment({})
    for raw in ['[]','{}','null','invalid',json.dumps([row | {'expense_input':1.1}]),json.dumps([row | {'extra':'unknown'}])]:
        with pytest.raises(AdmissionDenied):catalog_from_environment({'VMODEL_REVIEWED_MODEL_PRICES':raw})


def test_serialized_bound_must_cover_exact_bytes_and_not_messages_only():
    raw=b'{"model":"fake","tools":["schema"],"max_tokens":10}'
    kwargs=dict(stage='needs',provider='fake',model='fake',serialized_request=raw,max_output_tokens=10)
    with pytest.raises(AdmissionDenied):ReviewedCatalog([P],{('fake','fake'):lambda **kw:RequestBound('TEST',12,10,raw,True)}).resolve_serialized(**kwargs)
    for bound in [RequestBound('TEST',12,10,b'different',True),RequestBound('TEST',12,10,raw,False)]:
        with pytest.raises(AdmissionDenied):ReviewedCatalog([P],{('serialized','fake','fake'):lambda **kw:bound}).resolve_serialized(**kwargs)
    catalog=ReviewedCatalog([P],{('serialized','fake','fake'):lambda **kw:RequestBound('TEST',12,10,kw['serialized_request'],True)})
    import hashlib
    assert catalog.resolve_serialized(**kwargs)[1]==hashlib.sha256(raw).hexdigest()


def test_cache_catalog_requires_explicit_categories_and_pair_of_rates():
    from dataclasses import replace
    cache=replace(P,usage_categories=('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens'),expense_cache_hit=1,expense_cache_miss=5)
    raw=b'complete serialized fake'
    catalog=ReviewedCatalog([cache],{('serialized','fake','fake'):lambda **kw:RequestBound('TEST',10,10,kw['serialized_request'],True)})
    quote,_=catalog.resolve_serialized(stage='needs',provider='fake',model='fake',serialized_request=raw,max_output_tokens=10)
    assert quote['expense_cache_hit']==1 and quote['expense_cache_miss']==5
    for invalid in [replace(cache,expense_cache_hit=None),replace(cache,expense_cache_miss=True),replace(P,expense_cache_hit=1,expense_cache_miss=5)]:
        with pytest.raises(AdmissionDenied):ReviewedCatalog([invalid])
