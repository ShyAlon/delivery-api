import pytest
from delivery_api.deepseek_usage import normalize
from delivery_api.attempt_ledger import AdmissionDenied

RAW={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20,'prompt_tokens_details':{'cached_tokens':80},'completion_tokens_details':{'reasoning_tokens':5}}


def test_cache_partition_reasoning_subset_not_double_counted():
    usage=normalize(RAW)
    assert usage['input_tokens']==100 and usage['output_tokens']==20
    assert usage['cache_hit_tokens']+usage['cache_miss_tokens']==usage['input_tokens']
    assert usage['reasoning_tokens']==5 and usage['reasoning_included_in_output'] is True
    assert usage['raw_usage']==RAW


def test_missing_reasoning_is_unknown_not_zero():
    raw=dict(RAW);raw.pop('completion_tokens_details')
    assert normalize(raw)['reasoning_tokens'] is None


@pytest.mark.parametrize('change',[{'prompt_cache_hit_tokens':81},{'total_tokens':121},{'completion_tokens':True},{'prompt_tokens_details':{'cached_tokens':79}},{'completion_tokens_details':{'reasoning_tokens':21}},{'completion_tokens_details':{'reasoning_tokens':-1}},{'unknown':1}])
def test_conflicting_or_unknown_usage_fails_closed(change):
    with pytest.raises(AdmissionDenied):normalize(RAW|change)
