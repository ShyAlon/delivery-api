"""Resume of a stopped item 6 run from its own restored ledger.

Read-only discovery of the stopped state, the typed confirmation text, and the two
write-offs of unknown items (a provider call that never started, and the failed job's
unmeasured compute time). Nothing here authorizes anything: the write-offs run only after
the caller passes a reference built from the operator's typed answer.
"""
import hashlib,json,time
from delivery_api.attempt_ledger import AdmissionDenied

MEASURED=('usage','raw_usage','raw_usage_received_at','expense_actual','retail_actual','settled_at')
ATTEMPT_ACTION='item6.provider.unknown_failure_waived'
RECONCILED_ACTION='item6.provider.reconciled'


def discover(c):
    """Exact facts of the stopped run. c.execute(sql,params).fetchall() must return dict rows."""
    jobs=c.execute('SELECT job_id,kind,state,task_id,attempt,generation,session_id,auth_session_id FROM worker_jobs').fetchall()
    by_kind={}
    for j in jobs:by_kind.setdefault(j['kind'],[]).append(j)
    if set(by_kind)!={'phase:intent','phase:needs'} or len(by_kind['phase:intent'])!=1:
        raise AdmissionDenied('exact stopped job chain required: one phase:intent and one phase:needs job')
    intent=by_kind['phase:intent'][0]
    if intent['state']!='done' or any(j['state']!='failed' for j in by_kind['phase:needs']):raise AdmissionDenied('completed intent job and failed needs job required')
    if any(intent['task_id']!=j['task_id'] for j in by_kind['phase:needs']):raise AdmissionDenied('one task required')
    attempts=c.execute('SELECT * FROM provider_attempts').fetchall()
    held=[a for a in attempts if a['state'] not in ('settled','released')]  # released = provider provably never reached, billed 0
    if len(held)!=1:raise AdmissionDenied('exactly one unsettled provider attempt required, found %d'%len(held))
    a=held[0]
    # Earlier resumes may leave extra failed needs jobs. Only the job that owns the held attempt is the stopped one;
    # each extra must be failed and every provider attempt it made must be settled (measured and already charged).
    owners=[j for j in by_kind['phase:needs'] if j['job_id']==a['job_id']]
    if len(owners)!=1:raise AdmissionDenied('the unsettled attempt must be a never-started reservation of a failed needs job')
    needs=owners[0]
    for extra in (j for j in by_kind['phase:needs'] if j is not needs):
        if any(x['job_id']==extra['job_id'] and x['state'] not in ('settled','released') for x in attempts):raise AdmissionDenied('extra failed needs job '+extra['job_id']+' has an unsettled provider attempt and needs its own review')
    waived=a['state']=='failure_waived'
    if waived:
        # A later resume of a run whose write-offs an earlier resume already made (typed yes) and recorded in the audit log.
        if a['job_id']!=needs['job_id']:raise AdmissionDenied('the written-off attempt must belong to the failed needs job')
        done=c.execute("SELECT action FROM audit_log WHERE detail::text LIKE %s AND action IN (%s,%s)",('%'+a['attempt_id']+'%',ATTEMPT_ACTION,RECONCILED_ACTION)).fetchall()
        if {r['action'] for r in done}!={ATTEMPT_ACTION,RECONCILED_ACTION}:raise AdmissionDenied('the written-off attempt has no complete audit record')
    elif a['state']!='reserved' or a['provider_started_at'] is not None or a['job_id']!=needs['job_id']:
        raise AdmissionDenied('the unsettled attempt must be a never-started reservation of the failed needs job')
    if any(a.get(k) is not None for k in MEASURED):raise AdmissionDenied('the unsettled attempt already has usage or settlement data')
    if c.execute('SELECT 1 FROM credit_charges WHERE attempt_id=%s',(a['attempt_id'],)).fetchone():raise AdmissionDenied('unexpected prepaid charge for the never-started attempt')
    if c.execute('SELECT 1 FROM retail_contributions WHERE contribution_key=%s',('attempt:'+a['attempt_id'],)).fetchone():raise AdmissionDenied('unexpected token contribution for the never-started attempt')
    if not waived and c.execute("SELECT 1 FROM audit_log WHERE detail::text LIKE %s AND action IN (%s,%s)",('%'+a['attempt_id']+'%',ATTEMPT_ACTION,RECONCILED_ACTION)).fetchone():raise AdmissionDenied('this attempt already has a write-off audit')
    key=needs['job_id']+':compute'
    seg=c.execute('SELECT state,deterministic_ms FROM retail_compute_segments WHERE segment_key=%s AND job_id=%s',(key,needs['job_id'])).fetchone()
    if waived:
        if not seg or seg['state']!='waived':raise AdmissionDenied('the written-off run needs its compute segment waived')
    elif not seg or seg['state']!='unresolved' or seg['deterministic_ms'] is not None:raise AdmissionDenied('the failed job needs an unresolved, unmeasured compute segment')
    extras=[j['job_id'] for j in by_kind['phase:needs'] if j is not needs]
    extra_compute=[]
    if extras:
        if c.execute("SELECT 1 FROM retail_contributions WHERE job_id=ANY(%s) AND contribution_key NOT LIKE 'attempt:%%' LIMIT 1",(extras,)).fetchone():raise AdmissionDenied('an extra failed needs job has a non-token retail contribution and needs its own review')
        for r in c.execute('SELECT segment_key,job_id,state,deterministic_ms,started_at FROM retail_compute_segments WHERE job_id=ANY(%s)',(extras,)).fetchall():
            if r.get('job_id') not in extras:continue
            if r['state']=='unresolved' and r['deterministic_ms'] is None:extra_compute.append({'job_id':r['job_id'],'segment_key':r['segment_key'],'started_at':float(r['started_at'])})
            elif r['state'] not in ('waived','settled'):raise AdmissionDenied('extra failed needs job '+r['job_id']+' has a compute segment in state '+r['state']+' that cannot be written off')
        if extra_compute and not waived:raise AdmissionDenied('the original write-offs must be done first; an extra failed job also has unmeasured compute')
    others=c.execute("SELECT 1 FROM retail_compute_segments WHERE job_id<>ALL(%s) AND state IN ('pending','unresolved') LIMIT 1",([needs['job_id']]+extras,)).fetchone()
    if others:raise AdmissionDenied('another compute segment is unresolved')
    accounts=c.execute('SELECT owner_id,balance_units,units_per_credit FROM credit_accounts').fetchall()
    if len(accounts)!=1 or accounts[0]['units_per_credit']!=10**9:raise AdmissionDenied('one prepaid account at the 1000000000 scale required')
    settled=[x for x in attempts if x['state']=='settled']
    return {'failed_jobs':sorted(j['job_id'] for j in by_kind['phase:needs']),'task_id':needs['task_id'],'owner_id':str(accounts[0]['owner_id']),'intent_job':intent['job_id'],'failed_job':needs['job_id'],
        'failed_job_session_id':str(needs['session_id']),'auth_session_id':str(needs['auth_session_id']),
        'attempt_id':a['attempt_id'],'attempt_state':a['state'],'expense_reserved':a['expense_reserved'],'retail_reserved':a['retail_reserved'],
        'compute_key':key,'settled_attempts':len(settled),'settled_expense_units':sum(x['expense_actual'] for x in settled),
        'balance_units':accounts[0]['balance_units'],'units_per_credit':accounts[0]['units_per_credit'],'already_written_off':waived,'extra_compute':sorted(extra_compute,key=lambda x:x['segment_key'])}


def facts_sha256(facts):
    return hashlib.sha256(json.dumps(facts,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def review_lines(facts,dump_sha256):
    usd=lambda units:'%.4f'%(units/10**12)
    if facts.get('already_written_off'):
        return ['Stopped run, restored from the database dump of an earlier resume (read only so far):',
        '  task '+facts['task_id']+', failed job '+facts['failed_job']+' (phase:needs)',
        '  settled provider calls: %d, confirmed provider spend $%s'%(facts['settled_attempts'],usd(facts['settled_expense_units'])),
        '  credit balance: %.9f credits'%(facts['balance_units']/facts['units_per_credit']),
        *(['  earlier resumes left %d extra failed needs job(s), every provider call of theirs settled and charged: %s'%(len(facts['failed_jobs'])-1,', '.join(j for j in facts['failed_jobs'] if j!=facts['failed_job']))] if len(facts.get('failed_jobs',[]))>1 else []),
        'Both unknown items were ALREADY written off by you in an earlier resume (audit records present); nothing is written off again:',
        '  1. provider call '+facts['attempt_id']+': never started, billed 0.',
        '     Its reservation of $%s stays counted against the spend ceiling.'%usd(facts['expense_reserved']),
        '  2. unmeasured compute time of the failed job ('+facts['compute_key']+'): billed 0.',
        *(['  unmeasured compute time of earlier failed resume job(s) (their provider calls are settled and charged; only the compute time was never measured), would be written off, billed 0 (needs your typed yes below):']+['    - job '+x['job_id']+' ('+x['segment_key']+'), started at '+time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(x['started_at'])) for x in facts['extra_compute']] if facts.get('extra_compute') else []),
        'Database dump sha256: '+dump_sha256,
        'Facts sha256: '+facts_sha256(facts)]
    return ['Stopped run, restored from its own database dump (read only so far):',
        '  task '+facts['task_id']+', failed job '+facts['failed_job']+' (phase:needs)',
        '  settled provider calls: %d, confirmed provider spend $%s'%(facts['settled_attempts'],usd(facts['settled_expense_units'])),
        '  credit balance: %.9f credits'%(facts['balance_units']/facts['units_per_credit']),
        'Two unknown items would be written off, billed 0, kept visible in the audit log:',
        '  1. provider call '+facts['attempt_id']+': reserved, never started (no provider start time, no usage, no charge).',
        '     Its reservation of $%s stays counted against the spend ceiling.'%usd(facts['expense_reserved']),
        '  2. unmeasured compute time of the failed job ('+facts['compute_key']+'): billed 0.',
        'Database dump sha256: '+dump_sha256,
        'Facts sha256: '+facts_sha256(facts)]


def confirm(facts,dump_sha256,run_id,*,ask=input,say=print,now=None):
    """The operator's own typed answer is the only authority. Returns the reference to record."""
    for line in review_lines(facts,dump_sha256):say(line)
    answer=ask('Type yes (English keyboard) to write off exactly these two items, anything else stops: ')
    if answer.strip()!='yes':raise AdmissionDenied('write-offs not confirmed; nothing was written')
    stamp=now or time.strftime('%Y-%m-%dT%H:%M:%S%z')
    return 'typed-yes:%s:run-%s:attempt-%s:facts-%s'%(stamp,run_id,facts['attempt_id'][:8],facts_sha256(facts)[:12])


def confirm_extra(facts,dump_sha256,run_id,*,ask=input,say=print,now=None):
    """Typed yes for the unmeasured compute of earlier failed resume jobs only (billed 0). The review lines were already shown."""
    n=len(facts['extra_compute'])
    answer=ask('Type yes (English keyboard) to write off exactly these %d compute segment(s) (billed 0), anything else stops: '%n)
    if answer.strip()!='yes':raise AdmissionDenied('write-off not confirmed; nothing was written')
    stamp=now or time.strftime('%Y-%m-%dT%H:%M:%S%z')
    return 'typed-yes:%s:run-%s:extra-compute-%s:facts-%s'%(stamp,run_id,facts['extra_compute'][0]['job_id'][:8],facts_sha256(facts)[:12])


def waive_extra_compute(store,item,*,authorization_ref,dump_sha256):
    if not authorization_ref.startswith('typed-yes:') or len(dump_sha256)!=64:raise AdmissionDenied('typed confirmation reference and dump hash required')
    from recovery_accounting import waive_unknown_compute
    return waive_unknown_compute(store,item['segment_key'],original_dump_sha256=dump_sha256,authorization_ref=authorization_ref)


def waive_attempt(store,facts,*,authorization_ref,dump_sha256):
    if not authorization_ref.startswith('typed-yes:') or len(dump_sha256)!=64:raise AdmissionDenied('typed confirmation reference and dump hash required')
    from delivery_api.postgres_store import _transactional
    from delivery_api.prepaid import owner_lock
    from delivery_api.tenancy import current_scope
    @_transactional
    def write(store):
        store._db.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)');owner_lock(store)
        r=store._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=? FOR UPDATE',(facts['attempt_id'],)).fetchone()
        job=store.worker_job(facts['failed_job']);scope=current_scope()
        if not r or r['job_id']!=facts['failed_job'] or not job or job['state']!='failed' or str(job['auth_session_id'])!=scope.session_id or scope.owner_id!=facts['owner_id']:
            raise AdmissionDenied('exact scoped failed job and attempt required')
        prior=store._db.execute("SELECT detail FROM audit_log WHERE task_id=? AND action=? AND detail::jsonb->>'attempt_id'=?",(job['task_id'],ATTEMPT_ACTION,facts['attempt_id'])).fetchall()
        if r['state']=='failure_waived':
            if len(prior)!=1:raise AdmissionDenied('ambiguous write-off replay')
            proof=json.loads(prior[0]['detail'])
            if proof['authorization_ref']!=authorization_ref:raise AdmissionDenied('changed write-off replay')
            return proof|{'replayed':True}
        if r['state']!='reserved' or prior or r['provider_started_at'] is not None or any(r.get(k) is not None for k in MEASURED):raise AdmissionDenied('only the exact never-started reservation may be written off')
        if r['expense_reserved']!=facts['expense_reserved']:raise AdmissionDenied('reviewed reservation changed')
        if store._db.execute('SELECT 1 FROM credit_charges WHERE attempt_id=?',(facts['attempt_id'],)).fetchone():raise AdmissionDenied('unexpected prepaid charge')
        if store._db.execute('SELECT 1 FROM retail_contributions WHERE contribution_key=?',('attempt:'+facts['attempt_id'],)).fetchone():raise AdmissionDenied('unexpected token contribution')
        account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(scope.owner_id,)).fetchone()
        if account['balance_units']!=facts['balance_units']:raise AdmissionDenied('reviewed balance changed')
        store._db.execute("UPDATE provider_attempts SET state='failure_waived',updated_at=? WHERE attempt_id=? AND state='reserved'",(time.time(),facts['attempt_id']))
        proof={'attempt_id':facts['attempt_id'],'failed_job':facts['failed_job'],'dump_sha256':dump_sha256,'authorization_ref':authorization_ref,
            'usage':'unknown','never_started':True,'retail_debit_units':0,'provider_expense_actual':None,'provider_debit_confirmed':False,
            'conservative_cap_consumption_units':r['expense_reserved'],'balance_unchanged':True,'failed_job_unchanged':True}
        store.audit(scope.owner_id,ATTEMPT_ACTION,job['task_id'],proof)
        # The continuation check looks for a reconciliation record naming the failed job.
        store.audit(scope.owner_id,RECONCILED_ACTION,job['task_id'],{'attempt_id':facts['attempt_id'],'plan':{'job_id':facts['failed_job'],'disposition':'never_started_waived'},
            'authorization_ref':authorization_ref,'dump_sha256':dump_sha256,'failed_job_unchanged':True})
        return proof|{'replayed':False}
    return write(store)


def waive_compute(store,facts,*,authorization_ref,dump_sha256):
    if not authorization_ref.startswith('typed-yes:') or len(dump_sha256)!=64:raise AdmissionDenied('typed confirmation reference and dump hash required')
    from recovery_accounting import waive_unknown_compute
    return waive_unknown_compute(store,facts['compute_key'],original_dump_sha256=dump_sha256,authorization_ref=authorization_ref)
