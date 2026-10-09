"""Trusted-worker ledger mechanics, not an exposed provider API or price resolver.

All money is fixed-point integers in separately named expense/retail units.
Reviewed limits are operator-provisioned. Quote construction is trusted delivery
work; no producer/public caller may supply a price or authoritative token bound.
"""
import hashlib,json,time
from .tenancy import current_scope,TenantAccessDenied
from .worker_capability import WorkerCapability

class AdmissionDenied(ValueError):pass

def _integer(value):
    return isinstance(value,int) and not isinstance(value,bool) and value>=0

def _quote(quote):
    keys={'provider','model','price_version','bound_version','input_max','output_max',
          'expense_input','expense_output','retail_input','retail_output'}
    optional={'retail_units_per_credit','expense_cache_hit','expense_cache_miss'}
    if bool('expense_cache_hit' in quote)!=bool('expense_cache_miss' in quote):raise AdmissionDenied('complete reviewed cache rates required')
    if any(not _integer(quote[k]) for k in ('expense_cache_hit','expense_cache_miss') if k in quote):raise AdmissionDenied('integer cache prices required')
    if set(quote)-optional!=keys or ('retail_units_per_credit' in quote and (not _integer(quote['retail_units_per_credit']) or not quote['retail_units_per_credit'])) or any(not isinstance(quote[k],str) or not quote[k] for k in keys if k.endswith('version') or k in ('provider','model')):
        raise AdmissionDenied('reviewed quote and exact request bound required')
    if any(not _integer(quote[k]) for k in keys if k not in ('provider','model','price_version','bound_version')):
        raise AdmissionDenied('quote amounts and token bounds must be nonnegative integers')
    if not quote['output_max']:raise AdmissionDenied('positive output bound required')
    input_rate=max(quote.get('expense_cache_hit',quote['expense_input']),quote.get('expense_cache_miss',quote['expense_input']))
    return (quote['input_max']*input_rate+quote['output_max']*quote['expense_output'],
            quote['input_max']*quote['retail_input']+quote['output_max']*quote['retail_output'])

def _job(store,job_id):
    job=store.worker_job(job_id)
    if not job or job['state']!='running':raise AdmissionDenied('claimed worker job required')
    capability=WorkerCapability.capture(store,job['task_id']);capability.validate(store)
    if job['generation']!=capability.generation or job['session_id']!=capability.session_id or str(job['auth_session_id'])!=capability.scope.session_id:
        raise TenantAccessDenied('attempt worker authority changed')
    return job

def reserve(store,job_id,attempt_id,quote,request_digest,*,require_prepaid=False):
    expense,retail=_quote(quote)
    if not isinstance(request_digest,str) or not request_digest:raise AdmissionDenied('exact request digest required')
    _job(store,job_id);owner=current_scope().owner_id
    # Shared owner lock serializes independent processes, not just one API worker.
    lock_key=int.from_bytes(hashlib.sha256(owner.encode()).digest()[:8],'big',signed=True)
    store._db.execute('SELECT pg_advisory_xact_lock(?)',(lock_key,)).fetchone()
    # Authority can change while waiting on the shared Stop/admission lock.
    # READ COMMITTED re-read after the lock, before replay or any new hold.
    _job(store,job_id)
    from .suspension_policy import require_task_operation
    require_task_operation(store,'provider.reserve')
    limits=store._db.execute('SELECT * FROM admission_limits WHERE owner_id=?',(owner,)).fetchone()
    if not limits:raise AdmissionDenied('reviewed owner admission configuration missing')
    fingerprint=hashlib.sha256(json.dumps({'job':job_id,'quote':quote,'request':request_digest},sort_keys=True).encode()).hexdigest()
    existing=store.provider_attempt(attempt_id)
    if existing:
        if existing['fingerprint']!=fingerprint:raise AdmissionDenied('conflicting admission replay')
        return existing
    now=time.time()
    rows=store._db.execute('SELECT * FROM provider_attempts WHERE owner_id=?',(owner,)).fetchall()
    held=[r for r in rows if r['state'] in ('reserved','dispatched','uncertain')]
    if any(r['state']=='uncertain' for r in held):raise AdmissionDenied('uncertain provider usage requires review')
    if len(held)>=limits['concurrency_max']:raise AdmissionDenied('owner attempt concurrency exhausted')
    if limits['rate_max'] is not None and sum(r['created_at']>now-limits['rate_window_seconds'] for r in rows)>=limits['rate_max']:
        raise AdmissionDenied('owner attempt rate exhausted')
    # Outstanding holds survive period reset; only settled expense ages out.
    active=held+[r for r in rows if r['state']=='settled' and r['created_at']>now-limits['period_seconds']]
    used_expense=sum(r['expense_actual'] if r['state']=='settled' else r['expense_reserved'] for r in active)
    used_retail=sum(r['retail_actual'] if r['state']=='settled' else r['retail_reserved'] for r in active)
    if (limits['attempt_expense_max'] is not None and expense>limits['attempt_expense_max']) or (not require_prepaid and limits['attempt_retail_max'] is not None and retail>limits['attempt_retail_max']):
        raise AdmissionDenied('attempt exceeds reviewed maximum')
    if (limits['expense_limit'] is not None and used_expense+expense>limits['expense_limit']) or (not require_prepaid and limits['retail_limit'] is not None and used_retail+retail>limits['retail_limit']):
        raise AdmissionDenied('owner spend allowance exhausted')
    store._db.execute('INSERT INTO provider_attempts (attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,usage,created_at,updated_at) VALUES (?,?,?,?,?, ?,\'reserved\',?,?,NULL,NULL,NULL,?,?)',
      (attempt_id,owner,job_id,limits['config_version'],json.dumps(quote),fingerprint,expense,retail,now,now))
    return store.provider_attempt(attempt_id)

def transition(store,attempt_id,action,usage=None,*,require_prepaid=False):
    # Same lock order as admission: owner first, attempt/account rows second.
    from .prepaid import owner_lock
    owner_lock(store)
    row=store._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=? FOR UPDATE',(attempt_id,)).fetchone()
    if not row:raise AdmissionDenied('owner attempt not found')
    if action=='start':
        from .suspension_policy import require_task_operation
        require_task_operation(store,'provider.dispatch')
        _job(store,row['job_id'])
        if row['state']!='reserved':raise AdmissionDenied('attempt already dispatched or closed')
        if require_prepaid:
            from .prepaid import authorize
            authorize(store,dict(row))
        store._db.execute("UPDATE provider_attempts SET state='dispatched',updated_at=? WHERE attempt_id=?",(time.time(),attempt_id))
    elif action=='cancel':
        if row['state']=='cancelled':return dict(row)
        if row['state']!='reserved':raise AdmissionDenied('dispatched or uncertain funds cannot be released')
        store._db.execute("UPDATE provider_attempts SET state='cancelled',updated_at=? WHERE attempt_id=?",(time.time(),attempt_id))
    elif action=='uncertain':
        if row['state'] not in ('dispatched','uncertain'):raise AdmissionDenied('only dispatched attempts can be uncertain')
        store._db.execute("UPDATE provider_attempts SET state='uncertain',updated_at=? WHERE attempt_id=?",(time.time(),attempt_id))
    elif action=='settle':
        if row['state']=='settled':
            if row['usage']!=usage:raise AdmissionDenied('conflicting settlement replay')
            return dict(row)
        if row['state']!='dispatched':raise AdmissionDenied('dispatched attempt required for settlement')
        quote=row['quote']
        categories={'input_tokens','output_tokens'}
        if 'expense_cache_hit' in quote:categories|={'cache_hit_tokens','cache_miss_tokens'}
        valid=isinstance(usage,dict) and set(usage)==categories and all(_integer(n) for n in usage.values())
        if valid and 'cache_hit_tokens' in usage:valid=usage['cache_hit_tokens']+usage['cache_miss_tokens']==usage['input_tokens']
        if not valid or usage['input_tokens']>quote['input_max'] or usage['output_tokens']>quote['output_max']:
            # Return the durable hold, not an exception that rolls it back.
            store._db.execute("UPDATE provider_attempts SET state='uncertain',updated_at=? WHERE attempt_id=?",(time.time(),attempt_id))
            return store.provider_attempt(attempt_id)
        expense=(usage['cache_hit_tokens']*quote['expense_cache_hit']+usage['cache_miss_tokens']*quote['expense_cache_miss'] if 'expense_cache_hit' in quote else usage['input_tokens']*quote['expense_input'])+usage['output_tokens']*quote['expense_output']
        retail=usage['input_tokens']*quote['retail_input']+usage['output_tokens']*quote['retail_output']
        from .prepaid import debit
        debit(store,dict(row),retail)
        store._db.execute("UPDATE provider_attempts SET state='settled',expense_actual=?,retail_actual=?,usage=?::jsonb,updated_at=?,settled_at=? WHERE attempt_id=?",(expense,retail,json.dumps(usage),time.time(),time.time(),attempt_id))
    else:raise AdmissionDenied('unsupported attempt transition')
    return store.provider_attempt(attempt_id)
