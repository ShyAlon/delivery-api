"""Frozen diagnostic provider expense, never a debit or invoice claim.

Inputs come from the reviewed parent quote and measured provider token buckets.
No current-price lookup, raw request/response retention or historical backfill.
"""
from decimal import Decimal, localcontext
from .provider_snapshot import ProviderSnapshot


def frozen_expense(quote,usage,started_at,finished_at):
    """Return exact fixed-point arithmetic and the existing interval-rate basis."""
    if not isinstance(quote,dict) or not isinstance(usage,dict):
        raise ValueError('frozen quote and normalized usage required')
    scale=quote.get('expense_units_per_usd')
    # ProviderSnapshot expresses its rates in 10**12 USD units per token.
    if type(scale) is not int or scale!=10**12:
        raise ValueError('snapshot-compatible frozen USD scale required')
    evidence=quote.get('provider_evidence')
    if not isinstance(evidence,dict):
        raise ValueError('frozen provider snapshot required')
    snapshot=ProviderSnapshot(evidence['snapshot'])
    if snapshot.id!=evidence.get('snapshot_sha256') or snapshot.data['model']!=quote.get('model'):
        raise ValueError('provider snapshot identity mismatch')
    categories=('cache_hit_tokens','cache_miss_tokens','output_tokens','input_tokens')
    if any(type(usage.get(k)) is not int or usage[k]<0 for k in categories):
        raise ValueError('measured cache and output tokens required')
    if usage['cache_hit_tokens']+usage['cache_miss_tokens']!=usage['input_tokens']:
        raise ValueError('inconsistent measured input tokens')
    band,rates=snapshot.billed_rates(started_at,finished_at)
    amount=sum(usage[k]*rates[r] for k,r in (
        ('cache_hit_tokens','cache_hit'),('cache_miss_tokens','cache_miss'),('output_tokens','output')))
    with localcontext() as context:
        context.prec=80
        usd=str(Decimal(amount)/Decimal(scale))
    return {'status':'calculated_frozen_rates','expense_units':str(amount),'expense_units_per_usd':scale,
        'provider_expense_usd':usd,'band':band,'rates':dict(rates),
        'snapshot_sha256':snapshot.id,
        'basis':'existing conservative UTC call-interval rate rule; not provider invoice confirmation'}


def call_evidence(call,usage,quote,started_at,finished_at,duration_s):
    """Build bounded call facts; unknown prices never mask a successful answer."""
    from math import isfinite
    if type(duration_s) not in (int,float) or not isfinite(duration_s) or duration_s<0:
        raise ValueError('measured nonnegative duration required')
    metadata=call.metadata
    result={'version':'call-metadata-v1','logical_call_id':call.call_id,
        'provider':call.provider,'model':call.model,
        'item':metadata.item if metadata is not None else None,
        'fields':list(metadata.fields) if metadata is not None else [],
        'latency_s':round(duration_s,6),'started_at':started_at.isoformat(),
        'finished_at':finished_at.isoformat(),
        'cache_hit_tokens':usage.get('cache_hit_tokens'),'cache_miss_tokens':usage.get('cache_miss_tokens'),
        'reasoning_tokens':usage.get('reasoning_tokens'),
        'provider_expense':{'status':'unknown_missing_frozen_quote'}}
    if quote is not None:
        try:
            if (quote.get('provider'),quote.get('model'))!=(call.provider,call.model):
                raise ValueError('quote differs from reviewed call route')
            if call.provider=='ollama' and all(quote.get(k)==0 for k in ('expense_input','expense_output')):
                result['provider_expense']={'status':'recorded_local_zero_rate','provider_expense_usd':'0'}
            else:result['provider_expense']=frozen_expense(quote,usage,started_at,finished_at)
        except (ValueError,KeyError,TypeError,RuntimeError):
            # Fixed status, no exception prose, request/response or parent secrets.
            result['provider_expense']={'status':'unknown_invalid_frozen_evidence'}
    return result


def check_installed_schema(dsn):
    """Catalog-only readiness for legacy/local journals, before any job starts."""
    from .db_pool import pool_for
    with pool_for(dsn).connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute("SELECT to_regprocedure('public.requi_call_evidence_version()') IS NOT NULL AS ready").fetchone()
        if not (row['ready'] if isinstance(row,dict) else row[0]):
            raise ValueError('call evidence migration missing; run req migrate before starting')
