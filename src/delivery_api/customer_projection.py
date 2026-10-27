"""Customer JSON projection. Provider usage remains in internal source records."""

def without_provider_usage(value):
    if isinstance(value, dict):
        return {key: without_provider_usage(item) for key, item in value.items()
                if not _usage_key(key)}
    if isinstance(value, list):
        return [without_provider_usage(item) for item in value]
    return value


def _usage_key(key):
    normalized = str(key).lower()
    return (normalized in {'usage', 'usage_metadata', 'usage_segments', 'token_usage',
                           'tokens', 'cache_hit', 'cache_miss', 'raw_usage', 'raw_usage_received_at'}
            or normalized.endswith('_tokens'))


def without_model_pricing(value):
    """Internal routing/billing metadata only, not arbitrary customer prose."""
    if isinstance(value,dict):
        hidden={'provider','model','model_map','effective_models','effective_stages','model_default','model_route','route','route_history','from_model_map','to_model_map','resolved','rates','price_version','bound_version'}
        projected={key:without_model_pricing(item) for key,item in value.items() if key not in hidden}
        if value.get('kind')=='model_route':
            projected['message']='Processing configuration updated'
            projected['payload']={key:item for key,item in projected.get('payload',{}).items() if key in ('scope','actor','sequence','timestamp','transcript')}
        return projected
    if isinstance(value,list):return [without_model_pricing(item) for item in value]
    return value


def audit_credits_only(value):
    """Audit data keeps credit values, not USD or provider expense fields.

    This is a projection only; internal ledger/audit rows stay unchanged.
    """
    if isinstance(value,dict):
        return {key:audit_credits_only(item) for key,item in value.items()
                if not (str(key).lower().endswith('_usd') or str(key).lower().startswith(('expense_','interview_cost','interview_calls','interview_unpriced')))}
    if isinstance(value,list):return [audit_credits_only(item) for item in value]
    return value
