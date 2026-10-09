import pytest
from delivery_api.launch_admission_policy import validate_owner_limits,validate_launch_quote
from delivery_api.attempt_ledger import AdmissionDenied

LIMITS={'config_version':'reviewed-test-only','expense_limit':None,'retail_limit':None,
        'attempt_expense_max':None,'attempt_retail_max':None,'rate_max':None,'concurrency_max':3}
QUOTE={'provider':'deepseek','model':'fake-model','price_version':'test-only','bound_version':'test-only',
       'input_max':100000,'output_max':10000,'expense_input':2,'expense_output':3,
       'expense_cache_hit':1,'expense_cache_miss':2,'retail_input':4,'retail_output':5,'retail_units_per_credit':1000000}


def test_launch_disabled_caps_preserve_exact_three_slots():
    assert validate_owner_limits(LIMITS)==LIMITS
    for key in ('expense_limit','retail_limit','attempt_expense_max','attempt_retail_max','rate_max'):
        for value in (0,1,True):
            with pytest.raises(AdmissionDenied):validate_owner_limits(LIMITS|{key:value})
        with pytest.raises(AdmissionDenied):validate_owner_limits({k:v for k,v in LIMITS.items() if k!=key})
    for count in (None,True,1,2,4,3.0,'3'):
        with pytest.raises(AdmissionDenied):validate_owner_limits(LIMITS|{'concurrency_max':count})


def test_launch_quotes_require_deepseek_explicit_cache_prices_and_credit_scale():
    assert validate_launch_quote(QUOTE)==QUOTE
    for provider in ('openai','ollama','fake',''):
        with pytest.raises(AdmissionDenied):validate_launch_quote(QUOTE|{'provider':provider})
    for field in ('expense_cache_hit','expense_cache_miss','retail_units_per_credit'):
        with pytest.raises(AdmissionDenied):validate_launch_quote({k:v for k,v in QUOTE.items() if k!=field})


def test_no_default_provisioning_and_no_automatic_execution():
    for missing in ({},None,{'config_version':'unreviewed'}):
        with pytest.raises(AdmissionDenied):validate_owner_limits(missing)
    reviewed=validate_owner_limits(LIMITS)
    reviewed['rate_max']=1
    assert LIMITS['rate_max'] is None
