"""SELECT-only selected-task accounting pages. No provider payloads or secrets.

Historical gaps remain unknown. Rows are ordered by immutable stable identity;
no account-wide ledger tail, timestamp matching or current-price backfill.
"""
from decimal import Decimal
from datetime import datetime
from .tenancy import current_scope

PAGE_SIZE=250
# Fixed projections only: never SELECT * from a provider/worker/audit payload.
SOURCES={
 'answers':('answer_charge_receipts','answer_id',
  'x.answer_id,x.job_id,x.session_id,x.generation,x.state,x.estimate_units,x.actual_units,x.correction_units,x.units_per_credit,x.overrun,x.started_at,x.settled_at,x.pricing'),
 'answer_usage':('answer_charge_usage','attempt_id',
  "x.attempt_id,x.answer_id,x.evidence->>'phase' AS phase,x.evidence->'input_tokens' AS input_tokens,x.evidence->'output_tokens' AS output_tokens,x.evidence->'call_evidence' AS call_evidence"),
 'provider_attempts':('provider_attempts','attempt_id',
  "x.attempt_id,x.job_id,x.state,x.expense_actual,x.retail_actual,x.expense_reserved,x.retail_reserved,x.created_at,x.settled_at,x.quote,x.usage"),
 'retail_contributions':('retail_contributions','contribution_key',
  'x.contribution_key,x.job_id,x.phase_key,x.kind,x.input_tokens,x.output_tokens,x.deterministic_ms,x.exact_units,x.posted_units,x.units_per_credit,x.rates,x.created_at'),
}

def _safe(value):
 if isinstance(value,Decimal):return str(value)
 if isinstance(value,datetime):return value.isoformat()
 if isinstance(value,list):return [_safe(x) for x in value]
 if isinstance(value,dict):return {k:_safe(v) for k,v in value.items()}
 return value


def _call_facts(value):
 keys=('logical_call_id','item','provider','model','latency_s','started_at','finished_at','cache_hit_tokens','cache_miss_tokens','reasoning_tokens')
 result={k:None for k in keys};result.update(fields=[],field=None,provider_expense=None)
 if not isinstance(value,dict) or value.get('version')!='call-metadata-v1':return result
 for k in keys:result[k]=value.get(k)
 fields=value.get('fields');result['fields']=fields if isinstance(fields,list) else []
 result['field']=result['fields'][0] if len(result['fields'])==1 else None
 expense=value.get('provider_expense')
 if isinstance(expense,dict):
  result['provider_expense']={k:expense[k] for k in ('status','expense_units','expense_units_per_usd','provider_expense_usd','band','rates','snapshot_sha256','basis') if k in expense}
 return result


def page(store,task_id,source,after=''):
 if source not in SOURCES:raise ValueError('unknown accounting source')
 if not isinstance(after,str) or len(after)>512:raise ValueError('invalid accounting cursor')
 if not store.task(task_id):raise KeyError('owned task not found')
 table,key,columns=SOURCES[source]
 if source=='answer_usage':
  join="JOIN answer_charge_receipts a ON a.answer_id=x.answer_id JOIN worker_jobs w ON w.job_id=a.job_id LEFT JOIN retail_contributions r ON r.contribution_key='answer-token:'||md5(jsonb_build_array(x.owner_id,a.job_id,a.answer_id,x.attempt_id)::text)"
  columns+=",a.job_id,r.contribution_key,r.posted_units,r.units_per_credit,r.exact_units"
 else:join='JOIN worker_jobs w ON w.job_id=x.job_id'
 if source=='provider_attempts':
  join+=" LEFT JOIN retail_contributions r ON r.contribution_key='attempt:'||x.attempt_id"
  columns+=',r.contribution_key,r.posted_units,r.units_per_credit,ce.call_evidence'
  join+=" LEFT JOIN LATERAL (SELECT CASE WHEN count(*)=1 THEN (jsonb_agg(s.call_evidence)->0) ELSE NULL END AS call_evidence FROM staged_provider_responses s WHERE s.owner_id=x.owner_id AND s.attempt_id=x.attempt_id) ce ON true"

 rows=[dict(r) for r in store._execute_native(
  f'SELECT {columns} FROM {table} x {join} WHERE w.task_id=%s AND x.owner_id=%s AND x.{key}>%s ORDER BY x.{key} LIMIT %s',
  (task_id,current_scope().owner_id,after,PAGE_SIZE+1)).fetchall()]
 more=len(rows)>PAGE_SIZE;rows=rows[:PAGE_SIZE]
 for row in rows:
  if 'posted_units' in row and row.get('units_per_credit'):
   row['actual_retail_credits']=str(Decimal(row['posted_units'])/Decimal(row['units_per_credit']))
  if source in ('answer_usage','provider_attempts'):
   facts=_call_facts(row.pop('call_evidence',None));row.update(facts)
   row['join_provenance']=('exact answer settlement attempt/contribution key' if source=='answer_usage' else 'exact provider attempt/staged-response binding; ambiguous bindings remain unknown')
   row['call_evidence_status']='recorded' if facts['logical_call_id'] is not None else 'unknown_not_recorded'
  if source=='answers':
   pricing=row.get('pricing') or {};rates=pricing.get('rates') or {}
   row['pricing']={'version':pricing.get('version'),'start_credits':pricing.get('start_credits'),
    'rates':{k:rates[k] for k in ('formula_version','units_per_credit','price_per_token','price_per_10ms','usd_per_credit') if k in rates}}
  # Frozen numbers/route identities only. No arbitrary quote/usage fields escape.
  for field,allow in [('quote',('provider','model','expense_units_per_usd','retail_units_per_credit','expense_input','expense_output','expense_cache_hit','expense_cache_miss','retail_phase')),
                      ('usage',('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens'))]:
   if field in row:
    value=row[field];row[field]={k:value[k] for k in allow if k in value} if isinstance(value,dict) else None
  if source=='provider_attempts':
   scale=(row.get('quote') or {}).get('expense_units_per_usd')
   row['provider_expense_usd']=(str(Decimal(row['expense_actual'])/Decimal(scale)) if row.get('expense_actual') is not None and type(scale) is int and scale>0 else None)
   row['provider_expense_status']='recorded_frozen_quote' if row['provider_expense_usd'] is not None else 'unknown_missing_amount_or_scale'
   row['provider_expense']={'status':row['provider_expense_status'],'provider_expense_usd':row['provider_expense_usd']}
   for k in ('provider','model'):row[k]=(row.get('quote') or {}).get(k)
   for k in ('input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens'):row[k]=(row.get('usage') or {}).get(k)
 return {'task_id':task_id,'source':source,'rows':_safe(rows),'next_cursor':str(rows[-1][key]) if more else None,
  'complete':not more,'scope':'all recorded rows for the selected owned task, stable-id pagination; no live provider calls',
  'limits':{'page_size':PAGE_SIZE},'provenance':{
   'retail':'posted_units / units_per_credit; decimal fields are exact strings; frozen rates only',
   'provider_expense':'provider_attempts expense_actual uses its frozen quote scale; unknown when absent, never retail USD',
   'answer_v1_gaps':'historical calls remain unknown where identity/field/model/latency/frozen expense was not recorded; no time/order or current-price backfill',
   'settlement':'answer contribution rows are display evidence, not additional debits; receipt actual replaces estimate via correction'}}
