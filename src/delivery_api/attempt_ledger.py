"""Trusted-worker ledger mechanics, not an exposed provider API or price resolver.

All money is fixed-point integers in separately named expense/retail units.
Reviewed limits are operator-provisioned. Quote construction is trusted delivery
work; no producer/public caller may supply a price or authoritative token bound.
"""
import hashlib,json,time
from .tenancy import current_scope,TenantAccessDenied
from .worker_capability import WorkerCapability

class AdmissionDenied(ValueError):pass

RELEASE_ERRORS=('ConnectError','ConnectTimeout')
RELEASE_MAX_PER_JOB=3
RELEASE_MAX_PER_OWNER_DAY=10

def release_proof(row,evidence):
    """Exact proof that the provider never received the request. A connect failure happens while the
    connection is opened, before any request byte is written. Anything else (read or write error, any
    response, any unknown class) cannot be released and stays uncertain."""
    if not isinstance(evidence,dict) or set(evidence)!={'kind','error_class','bytes_sent','response_seen'}:raise AdmissionDenied('exact release proof required')
    if (evidence['kind']!='connect-failure' or evidence['error_class'] not in RELEASE_ERRORS
        or evidence['response_seen'] is not False or type(evidence['bytes_sent']) is not int or evidence['bytes_sent']!=0):
        raise AdmissionDenied('only a connect failure before any request byte can release an attempt')
    if row['state']!='dispatched':raise AdmissionDenied('only a dispatched attempt can be released')
    if any(row.get(k) is not None for k in ('raw_usage','usage','expense_actual','retail_actual','settled_at')):
        raise AdmissionDenied('an attempt with provider usage evidence cannot be released')
    return evidence

def _integer(value):
    return isinstance(value,int) and not isinstance(value,bool) and value>=0

def _quote(quote):
    keys={'provider','model','price_version','bound_version','input_max','output_max',
          'expense_input','expense_output','retail_input','retail_output'}
    optional={'retail_units_per_credit','expense_cache_hit','expense_cache_miss','expense_units_per_usd','retail_formula','provider_evidence','retail_phase','estimate_policy'}
    if 'estimate_policy' in quote:
        p=quote['estimate_policy']
        if set(p)!={'kind','ceiling','input_estimate','hard_output_cap','verified_input_bound'} or p['kind']!='complete-wire-bytes-plus4096-v1' or p['verified_input_bound'] is not False or p['input_estimate']!=quote['input_max'] or p['hard_output_cap']!=quote['output_max'] or p['input_estimate']+p['hard_output_cap']>p['ceiling']:raise AdmissionDenied('explicit reviewed estimate policy required')
    if 'retail_phase' in quote and (not isinstance(quote['retail_phase'],str) or not quote['retail_phase']):raise AdmissionDenied('retail phase identity required')
    if 'provider_evidence' in quote:
        from .provider_snapshot import ProviderSnapshot
        evidence=quote['provider_evidence'];snapshot=ProviderSnapshot(evidence['snapshot'])
        if snapshot.id!=evidence['snapshot_sha256']:raise AdmissionDenied('provider snapshot hash mismatch')
        ceiling=snapshot.ceiling()
        if (quote['expense_cache_hit'],quote['expense_cache_miss'],quote['expense_output'])!=(ceiling['cache_hit'],ceiling['cache_miss'],ceiling['output']):raise AdmissionDenied('provider admission ceiling mismatch')
    if 'retail_formula' in quote:
        from .retail_ledger import rates_from_snapshot
        rates=rates_from_snapshot(quote['retail_formula'])
        if quote.get('retail_units_per_credit')!=quote['retail_formula']['units_per_credit'] or quote['retail_input']!=quote['retail_output'] or quote['retail_input']!=int(rates.price_per_token*quote['retail_units_per_credit']):
            raise AdmissionDenied('retail integer compatibility rates differ from formula')
    if bool('expense_cache_hit' in quote)!=bool('expense_cache_miss' in quote):raise AdmissionDenied('complete reviewed cache rates required')
    if any(not _integer(quote[k]) for k in ('expense_cache_hit','expense_cache_miss') if k in quote):raise AdmissionDenied('integer cache prices required')
    if set(quote)-optional!=keys or ('retail_units_per_credit' in quote and (not _integer(quote['retail_units_per_credit']) or not quote['retail_units_per_credit'])) or any(not isinstance(quote[k],str) or not quote[k] for k in keys if k.endswith('version') or k in ('provider','model')):
        raise AdmissionDenied('reviewed quote and exact request bound required')
    if any(not _integer(quote[k]) for k in keys if k not in ('provider','model','price_version','bound_version')):
        raise AdmissionDenied('quote amounts and token bounds must be nonnegative integers')
    if 'expense_units_per_usd' in quote and (not _integer(quote['expense_units_per_usd']) or not quote['expense_units_per_usd']):raise AdmissionDenied('positive expense unit scale required')
    if not quote['output_max']:raise AdmissionDenied('positive output bound required')
    input_rate=max(quote.get('expense_cache_hit',quote['expense_input']),quote.get('expense_cache_miss',quote['expense_input']))
    return (quote['input_max']*input_rate+quote['output_max']*quote['expense_output'],
            quote['input_max']*quote['retail_input']+quote['output_max']*quote['retail_output'])

def _job(store,job_id):
    job=store.worker_job(job_id)
    if not job or job['state']!='running':raise AdmissionDenied('claimed worker job required')
    WorkerCapability.require_job(store,job)
    return job

def reserve(store,job_id,attempt_id,quote,request_digest,*,require_prepaid=False):
    expense,retail=_quote(quote)
    if not isinstance(request_digest,str) or not request_digest:raise AdmissionDenied('exact request digest required')
    # Same global-before-owner order as settlement and projection triggers.
    store._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    _job(store,job_id);owner=current_scope().owner_id
    # Shared owner lock serializes independent processes, not just one API worker.
    lock_key=int.from_bytes(hashlib.sha256(owner.encode()).digest()[:8],'big',signed=True)
    store._execute_native('SELECT pg_advisory_xact_lock(%s)',(lock_key,)).fetchone()
    # Authority can change while waiting on the shared Stop/admission lock.
    # READ COMMITTED re-read after the lock, before replay or any new hold.
    _job(store,job_id)
    from .suspension_policy import require_task_operation
    require_task_operation(store,'provider.reserve')
    limits=store._execute_native('SELECT * FROM admission_limits WHERE owner_id=%s',(owner,)).fetchone()
    if not limits:raise AdmissionDenied('reviewed owner admission configuration missing')
    fingerprint=hashlib.sha256(json.dumps({'job':job_id,'quote':quote,'request':request_digest},sort_keys=True).encode()).hexdigest()
    existing=store.provider_attempt(attempt_id)
    if existing:
        if existing['fingerprint']!=fingerprint:raise AdmissionDenied('conflicting admission replay')
        return existing
    now=time.time()
    totals=store._execute_native('SELECT * FROM public.requi_admission_totals(%s)',(now,)).fetchone()
    if totals['overruns']:raise AdmissionDenied('actual input exceeded estimate; new admission held for review')
    if 'retail_formula' in quote and store._execute_native("SELECT 1 FROM retail_compute_segments r JOIN worker_jobs w ON r.job_id=w.job_id WHERE r.owner_id=%s AND (r.state='unresolved' OR (r.state='pending' AND w.state<>'running')) LIMIT 1",(owner,)).fetchone():raise AdmissionDenied('unresolved compute accounting requires review')
    if totals['uncertain']:raise AdmissionDenied('uncertain provider usage requires review')
    if totals['held']>=limits['concurrency_max']:raise AdmissionDenied('owner attempt concurrency exhausted')
    if limits['rate_max'] is not None and totals['recent']>=limits['rate_max']:
        raise AdmissionDenied('owner attempt rate exhausted')
    used_expense=totals['used_expense'];used_retail=totals['used_retail']
    if (limits['attempt_expense_max'] is not None and expense>limits['attempt_expense_max']) or (not require_prepaid and limits['attempt_retail_max'] is not None and retail>limits['attempt_retail_max']):
        raise AdmissionDenied('attempt exceeds reviewed maximum')
    if (limits['expense_limit'] is not None and used_expense+expense>limits['expense_limit']) or (not require_prepaid and limits['retail_limit'] is not None and used_retail+retail>limits['retail_limit']):
        raise AdmissionDenied('owner spend allowance exhausted')
    store._execute_native("INSERT INTO provider_attempts (attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,usage,created_at,updated_at) VALUES (%s,%s,%s,%s,%s, %s,'reserved',%s,%s,NULL,NULL,NULL,%s,%s)",
      (attempt_id,owner,job_id,limits['config_version'],json.dumps(quote),fingerprint,expense,retail,now,now), write=True)
    return store.provider_attempt(attempt_id)

def transition(store,attempt_id,action,usage=None,*,require_prepaid=False):
    # Same lock order as admission: owner first, attempt/account rows second.
    from .prepaid import owner_lock
    # Same global-first ordering as launch dispatch. Accepted settlements never
    # consult admission limits and always finish, including after a tripwire.
    store._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    owner_lock(store)
    row=store._execute_native('SELECT * FROM provider_attempts WHERE attempt_id=%s FOR UPDATE',(attempt_id,)).fetchone()
    if not row:raise AdmissionDenied('owner attempt not found')
    if action=='start':
        if 'estimate_policy' in row['quote']:
            from .provider_balance import validate_balance
            balance=row.get('provider_balance_evidence')
            if not balance:raise AdmissionDenied('pre-call provider balance evidence required')
            validate_balance({'response':balance['response'],'observed_at':balance['observed_at']},row['expense_reserved'],row['quote']['expense_units_per_usd'])
        if 'provider_evidence' in row['quote']:
            from .provider_snapshot import ProviderSnapshot
            from datetime import datetime,timezone
            ProviderSnapshot(row['quote']['provider_evidence']['snapshot']).fresh(datetime.now(timezone.utc))
        from .suspension_policy import require_task_operation
        require_task_operation(store,'provider.dispatch')
        _job(store,row['job_id'])
        if row['state']!='reserved':raise AdmissionDenied('attempt already dispatched or closed')
        if row['quote'].get('provider')=='deepseek':
            import os
            if os.environ.get('DELIVERY_PAID_CHILD_PREVIEW')=='1':
                from .preview_spend import require
                require(store,dict(row))
            reason=store._execute_native('SELECT public.requi_own_test_expense_guard(%s,%s) AS reason',
                (row['job_id'],row['quote'].get('expense_units_per_usd'))).fetchone()['reason']
            if reason!='allowed':
                from .operational_alerts import emit
                emit('tripwire_breach' if reason=='own_test_rolling_24h_expense_limit' else 'unresolved_accounting',reason)
                raise AdmissionDenied(reason)
        if require_prepaid:
            from .prepaid import authorize
            authorize(store,dict(row))
        store._execute_native("UPDATE provider_attempts SET state='dispatched',updated_at=%s,provider_started_at=%s WHERE attempt_id=%s",(time.time(),time.time(),attempt_id), write=True)
    elif action=='cancel':
        if row['state']=='cancelled':return dict(row)
        if row['state']!='reserved':raise AdmissionDenied('dispatched or uncertain funds cannot be released')
        store._execute_native("UPDATE provider_attempts SET state='cancelled',updated_at=%s WHERE attempt_id=%s",(time.time(),attempt_id), write=True)
        # Audited in the same scoped transaction as the state change (the
        # outermost operation commits), mirroring the release audit below:
        # releasing a reservation frees money-relevant holds, so the release
        # and its reason must be traceable. usage carries optional caller
        # context - {'refusal': reason} from the pre-dispatch refusal cleanup.
        # Detail stays customer-projection safe: no reserved expense or rate
        # fields (the operator read joins the attempt row for those), and the
        # refusal text passes the log scrubber because a composed denial can
        # carry an underlying client message with connection detail.
        from .server_log_buffer import scrub
        detail={'attempt_id':attempt_id,'job_id':row['job_id']}
        if isinstance(usage,dict) and isinstance(usage.get('refusal'),str):detail['refusal']=scrub(usage['refusal'])[:300]
        store.audit(str(row['owner_id']),'provider.attempt.cancelled',store.worker_job(row['job_id'])['task_id'],detail)
    elif action=='uncertain':
        if row['state'] not in ('dispatched','uncertain'):raise AdmissionDenied('only dispatched attempts can be uncertain')
        store._execute_native("UPDATE provider_attempts SET state='uncertain',updated_at=%s WHERE attempt_id=%s",(time.time(),attempt_id), write=True)
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
        if not valid or (usage['input_tokens']>quote['input_max'] and 'estimate_policy' not in quote) or usage['output_tokens']>quote['output_max']:
            # Return the durable hold, not an exception that rolls it back.
            store._execute_native("UPDATE provider_attempts SET state='uncertain',updated_at=%s WHERE attempt_id=%s",(time.time(),attempt_id), write=True)
            return store.provider_attempt(attempt_id)
        if 'estimate_policy' in quote and usage['input_tokens']>quote['input_max']:
            store._execute_native('UPDATE provider_attempts SET input_estimate_overrun=true WHERE attempt_id=%s',(attempt_id,), write=True)
        settled_quote=quote
        if 'provider_evidence' in quote:
            from .provider_snapshot import ProviderSnapshot
            from datetime import datetime,timezone
            try:
                snapshot=ProviderSnapshot(quote['provider_evidence']['snapshot'])
                band,rates=snapshot.billed_rates(datetime.fromtimestamp(row['provider_started_at'],timezone.utc),datetime.now(timezone.utc))
            except (AdmissionDenied,TypeError):
                store._execute_native("UPDATE provider_attempts SET state='uncertain',updated_at=%s WHERE attempt_id=%s",(time.time(),attempt_id), write=True)
                return store.provider_attempt(attempt_id)
            settled_quote=dict(quote,expense_cache_hit=rates['cache_hit'],expense_cache_miss=rates['cache_miss'],expense_output=rates['output'])
            store._execute_native('UPDATE provider_attempts SET billed_rate_evidence=%s::jsonb WHERE attempt_id=%s',(json.dumps({'snapshot_sha256':snapshot.id,'band':band,'rates':rates,'basis':'uniform UTC start-to-settlement interval','settlement_observed_at':datetime.now(timezone.utc).isoformat()}),attempt_id), write=True)
        expense=(usage['cache_hit_tokens']*settled_quote['expense_cache_hit']+usage['cache_miss_tokens']*settled_quote['expense_cache_miss'] if 'expense_cache_hit' in quote else usage['input_tokens']*quote['expense_input'])+usage['output_tokens']*settled_quote['expense_output']
        retail=usage['input_tokens']*quote['retail_input']+usage['output_tokens']*quote['retail_output']
        if 'retail_formula' in quote:
            from .retail_ledger import post
            job=store.worker_job(row['job_id'])
            contribution=post(store,key='attempt:'+attempt_id,job_id=row['job_id'],phase=quote.get('retail_phase') or (job['phase_key'] if 'phase_key' in job else job['kind'].split(':')[-1]),kind='tokens',input_tokens=usage['input_tokens'],output_tokens=usage['output_tokens'],rates=quote['retail_formula'])
            retail=contribution['posted_units']
        from .prepaid import debit
        debit(store,dict(row),retail,already_posted='retail_formula' in quote)
        store._execute_native("UPDATE provider_attempts SET state='settled',expense_actual=%s,retail_actual=%s,usage=%s::jsonb,updated_at=%s,settled_at=%s WHERE attempt_id=%s",(expense,retail,json.dumps(usage),time.time(),time.time(),attempt_id), write=True)
        if 'expense_units_per_usd' in quote:
            # Atomically retain breach evidence with confirmed settlement. A
            # guard denial never rejects or undoes this accepted call's debit.
            store.confirmed_expense_admission(row['job_id'],quote['expense_units_per_usd'])
    elif action=='release':
        # usage carries the proof. Idempotent replay of an already released attempt.
        if row['state']=='released':return dict(row)
        evidence=release_proof(row,usage)
        now=time.time()
        per_job=store._execute_native("SELECT count(*) AS n FROM provider_attempts WHERE job_id=%s AND state='released'",(row['job_id'],)).fetchone()['n']
        per_owner=store._execute_native("SELECT count(*) AS n FROM provider_attempts WHERE owner_id=%s AND state='released' AND updated_at>%s",(row['owner_id'],now-86400)).fetchone()['n']
        if per_job>=RELEASE_MAX_PER_JOB or per_owner>=RELEASE_MAX_PER_OWNER_DAY:
            raise AdmissionDenied('release limit reached; the attempt stays held for review')
        store._execute_native("UPDATE provider_attempts SET state='released',updated_at=%s WHERE attempt_id=%s AND state='dispatched'",(now,attempt_id), write=True)
        store._execute_native("UPDATE credit_charges SET state='released' WHERE attempt_id=%s AND state='pending'",(attempt_id,), write=True)
        store.audit(str(row['owner_id']),'provider.attempt.released',store.worker_job(row['job_id'])['task_id'],
                    {'attempt_id':attempt_id,'job_id':row['job_id'],'evidence':evidence,'expense_reserved':row['expense_reserved'],'billed_units':0})
    else:raise AdmissionDenied('unsupported attempt transition')
    return store.provider_attempt(attempt_id)
