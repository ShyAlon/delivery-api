"""Pure stopped-run accounting preview; no network, mutation or execution."""
import hashlib,json
from datetime import datetime,timezone
from decimal import Decimal
from delivery_api.attempt_ledger import AdmissionDenied,_quote
from delivery_api.deepseek_usage import settlement_usage
from delivery_api.provider_snapshot import ProviderSnapshot
from delivery_api.retail_ledger import rates_from_snapshot


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def canonical_rows(attempt,job,account):
    # COPY extraction and native PG must bind identical values, not wire types.
    attempt=dict(attempt);job=dict(job);account=dict(account)
    for row in (attempt,job,account):
        for key in ('expense_reserved','retail_reserved','expense_actual','retail_actual','balance_units','units_per_credit','generation','attempt'):
            if key in row and row[key] is not None:row[key]=int(row[key])
        for key in ('created_at','updated_at','provider_started_at','raw_usage_received_at','settled_at'):
            if key in row and row[key] is not None:row[key]=float(row[key])
        for key in ('owner_id','auth_session_id'):
            if key in row:row[key]=str(row[key])
        for key in ('quote','raw_usage','usage'):
            if key in row and isinstance(row[key],str):row[key]=json.loads(row[key])
    return attempt,job,account


def accounting_preview(attempt,job,account):
    attempt,job,account=canonical_rows(attempt,job,account)
    if attempt['state']!='uncertain' or job['state']!='failed':
        raise AdmissionDenied('exact uncertain attempt and failed job required')
    if attempt['job_id']!=job['job_id'] or str(attempt['owner_id'])!=str(job['owner_id']) or str(account['owner_id'])!=str(job['owner_id']):
        raise AdmissionDenied('same-owner attempt/job/account required')
    if attempt.get('usage') is not None or attempt.get('settled_at') is not None or attempt.get('expense_actual') is not None or attempt.get('retail_actual') is not None:
        raise AdmissionDenied('unsettled accounting evidence required')
    quote=attempt['quote'];_quote(quote)
    usage=settlement_usage(attempt['raw_usage'])
    if usage['output_tokens']>quote['output_max'] or (usage['input_tokens']>quote['input_max'] and 'estimate_policy' not in quote):
        raise AdmissionDenied('usage outside reviewed hard bound')
    started=attempt.get('provider_started_at');received=attempt.get('raw_usage_received_at')
    if started is None or received is None:raise AdmissionDenied('dispatch and received usage timestamps required')
    snapshot=ProviderSnapshot(quote['provider_evidence']['snapshot'])
    if snapshot.id!=quote['provider_evidence']['snapshot_sha256']:raise AdmissionDenied('quoted snapshot hash mismatch')
    # Historic response interval, never delayed operator review time.
    start=datetime.fromtimestamp(float(started),timezone.utc);end=datetime.fromtimestamp(float(received),timezone.utc)
    snapshot.fresh(start)
    band,rates=snapshot.billed_rates(start,end)
    expense=usage['cache_hit_tokens']*rates['cache_hit']+usage['cache_miss_tokens']*rates['cache_miss']+usage['output_tokens']*rates['output']
    retail=rates_from_snapshot(quote['retail_formula'])
    if account['units_per_credit']!=quote['retail_formula']['units_per_credit']:raise AdmissionDenied('account scale mismatch')
    posted,remainder=retail.split_units(usage['input_tokens']+usage['output_tokens'],0,Decimal(str(account['fractional_debit_units'])))
    plan={'version':1,'attempt_id':attempt['attempt_id'],'job_id':job['job_id'],'owner_id':str(job['owner_id']),
          'task_id':job['task_id'],'session_id':job['session_id'],'auth_session_id':str(job['auth_session_id']),
          'source_attempt_sha256':digest({k:attempt.get(k) for k in ('attempt_id','owner_id','job_id','state','quote','raw_usage','provider_started_at','raw_usage_received_at','expense_actual','retail_actual','usage','settled_at')}),
          'source_account_sha256':digest({k:account.get(k) for k in ('owner_id','units_per_credit','balance_units')}|{'fractional_debit_units':str(Decimal(str(account['fractional_debit_units'])).normalize())}),
          'usage':usage,'expense_actual':expense,'expense_units_per_usd':quote['expense_units_per_usd'],
          'provider_usd':str(Decimal(expense)/quote['expense_units_per_usd']),
          'retail_posted_units':posted,'retail_credits':str(retail.credits(usage['input_tokens']+usage['output_tokens'])),
          'units_per_credit':account['units_per_credit'],'balance_before':account['balance_units'],
          'balance_after':account['balance_units']-posted,'fractional_after':str(remainder),
          'billed_rate_evidence':{'snapshot_sha256':snapshot.id,'band':band,'rates':rates,
              'basis':'historic dispatch-to-recorded-usage interval','provider_started_at':float(started),'raw_usage_received_at':float(received)},
          'provider_debit_confirmed':False,'new_provider_call':False,'failed_job_unchanged':True,
          'compute_disposition':'separate explicit unknown-measurement waiver required'}
    return plan|{'review_sha256':digest(plan)}
