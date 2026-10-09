"""Private launch-policy validation. Does not configure or enable dispatch.

Legacy ledger knobs remain useful for explicit test fixtures, but may not be
silently inherited by the prepaid launch gateway.
"""
from .attempt_ledger import AdmissionDenied

_DISABLED=('expense_limit','retail_limit','attempt_expense_max','attempt_retail_max','rate_max')


def validate_owner_limits(limits):
    if not isinstance(limits,dict) or not isinstance(limits.get('config_version'),str) or not limits['config_version']:
        raise AdmissionDenied('reviewed launch admission configuration required')
    if any(key not in limits or limits[key] is not None for key in _DISABLED):
        raise AdmissionDenied('launch requires no owner quota, attempt cap, or attempt-rate limit')
    if type(limits.get('concurrency_max')) is not int or limits['concurrency_max']!=3:
        raise AdmissionDenied('launch requires exactly three owner-wide concurrent calls')
    return dict(limits)


def validate_launch_quote(quote):
    from .attempt_ledger import _quote
    _quote(quote)
    if quote.get('provider')!='deepseek':
        raise AdmissionDenied('launch provider must be DeepSeek')
    if not {'expense_cache_hit','expense_cache_miss','retail_units_per_credit'}<=set(quote):
        raise AdmissionDenied('launch requires reviewed cache expense prices and prepaid unit scale')
    return dict(quote)
