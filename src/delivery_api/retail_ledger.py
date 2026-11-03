"""Once-only exact retail contributions, atomically posted under the owner lock."""
import hashlib,json,time
from decimal import Decimal
from .retail_pricing import RetailRates, UNITS_PER_CREDIT, decimal_value
from .tenancy import current_scope
from .attempt_ledger import AdmissionDenied
from .prepaid import owner_lock
from . import credit_audit
from .compute_segments import close_decision,releasable,release_decision


def rates_from_snapshot(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!={'formula_version','units_per_credit','price_per_token','price_per_10ms','usd_per_credit'} or snapshot['formula_version']!='tokens-plus-compute-v1' or snapshot['units_per_credit']!=UNITS_PER_CREDIT:
        raise AdmissionDenied('exact frozen retail formula snapshot required')
    try:return RetailRates(snapshot['price_per_token'],snapshot['price_per_10ms'],snapshot['usd_per_credit'])
    except ValueError as exc:raise AdmissionDenied(str(exc)) from exc


def post(store,*,key,job_id,phase,kind,input_tokens=0,output_tokens=0,deterministic_ms=0,rates):
    rates=rates_from_snapshot(rates)
    if not isinstance(key,str) or not 0<len(key)<=240 or kind not in ('tokens','compute'):
        raise AdmissionDenied('retail contribution identity required')
    if any(type(n) is not int or n<0 for n in (input_tokens,output_tokens)):
        raise AdmissionDenied('exact retail token usage required')
    ms=decimal_value(deterministic_ms,'deterministic_ms')
    if kind=='tokens' and ms or kind=='compute' and (input_tokens or output_tokens):
        raise AdmissionDenied('token and compute contribution must be separate')
    owner_lock(store)
    job=store.worker_job(job_id)
    if not job or str(job['owner_id'])!=current_scope().owner_id or str(job['auth_session_id'])!=current_scope().session_id:
        raise AdmissionDenied('same-owner contribution job required')
    payload={'job':job_id,'phase':phase,'kind':kind,'input':input_tokens,'output':output_tokens,'ms':str(ms),'rates':rates.snapshot()}
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    old=store._execute_native('SELECT * FROM retail_contributions WHERE contribution_key=%s',(key,)).fetchone()
    if old:
        if old['fingerprint']!=fingerprint:raise AdmissionDenied('conflicting retail contribution replay')
        return dict(old)
    account=store._execute_native('SELECT * FROM credit_accounts WHERE owner_id=%s FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account or account['units_per_credit']!=UNITS_PER_CREDIT:
        raise AdmissionDenied('exact prepaid scale migration required')
    units,remainder=rates.split_units(input_tokens+output_tokens,ms,account['fractional_debit_units'])
    if units>2**63-1 or account['balance_units']-units<-(2**63):
        raise AdmissionDenied('retail amount outside account integer range')
    exact=rates.credits(input_tokens+output_tokens,ms)*UNITS_PER_CREDIT
    now=time.time()
    store._execute_native('INSERT INTO retail_contributions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)',
        (key,current_scope().owner_id,job_id,phase,kind,input_tokens,output_tokens,ms,json.dumps(rates.snapshot()),exact,units,UNITS_PER_CREDIT,fingerprint,now), write=True)
    credit_audit.attribute(store._execute_native,'debit','system:retail_ledger',None,key,marker='%s')
    store._execute_native('UPDATE credit_accounts SET balance_units=balance_units-%s,fractional_debit_units=%s,updated_at=%s WHERE owner_id=%s',
        (units,remainder,now,current_scope().owner_id), write=True)
    return dict(store._execute_native('SELECT * FROM retail_contributions WHERE contribution_key=%s',(key,)).fetchone())


def phase_totals(store,job_id):
    """Display reads the debit evidence; it does not charge phases again."""
    rows=store._execute_native("SELECT phase_key,credits,usd,posted_units FROM retail_totals WHERE scope='job_phase' AND ref=%s AND n>0",(job_id,)).fetchall()
    return {r['phase_key']:{'credits':r['credits'],'usd':r['usd'],'posted_units':r['posted_units']} for r in rows}


def begin_compute(store,key,job_id,phase,rates):
    rates=rates_from_snapshot(rates).snapshot();owner_lock(store)
    job=store.worker_job(job_id)
    if not job or job['state']!='running':raise AdmissionDenied('running compute job required')
    if store._execute_native('SELECT 1 FROM retail_compute_segments WHERE segment_key=%s',(key,)).fetchone():
        raise AdmissionDenied('compute segment already begun; explicit recovery required')
    store._execute_native("INSERT INTO retail_compute_segments(segment_key,owner_id,job_id,phase_key,rates,state,started_at) VALUES (%s,%s,%s,%s,%s::jsonb,'pending',%s)",(key,current_scope().owner_id,job_id,phase,json.dumps(rates),time.time()), write=True)


def finish_compute(store,key,deterministic_ms):
    owner_lock(store)
    segment=store._execute_native('SELECT * FROM retail_compute_segments WHERE segment_key=%s FOR UPDATE',(key,)).fetchone()
    if not segment:raise AdmissionDenied('compute segment missing')
    if deterministic_ms is None:
        if segment['state']!='settled':store._execute_native("UPDATE retail_compute_segments SET state='unresolved' WHERE segment_key=%s",(key,), write=True)
        return None
    ms=decimal_value(deterministic_ms,'deterministic_ms')
    if segment['state']=='settled':
        if ms!=segment['deterministic_ms']:raise AdmissionDenied('conflicting compute replay')
        return dict(segment)
    if segment['state']=='unresolved':raise AdmissionDenied('unresolved compute needs explicit reconciliation')
    counts=store._execute_native("SELECT phase_key,count(*) AS calls FROM retail_contributions WHERE job_id=%s AND kind='tokens' GROUP BY phase_key ORDER BY phase_key",(segment['job_id'],)).fetchall()
    if not counts:counts=[{'phase_key':segment['phase_key'],'calls':1}]
    total=sum(r['calls'] for r in counts);remaining=ms;contributions=[]
    for i,row in enumerate(counts):
        share=remaining if i==len(counts)-1 else ms*row['calls']/total
        remaining-=share
        contributions.append(post(store,key='compute:'+key+':'+row['phase_key'],job_id=segment['job_id'],phase=row['phase_key'],kind='compute',deterministic_ms=share,rates=segment['rates']))
    contribution={'contributions':contributions,'deterministic_ms':ms}
    store._execute_native("UPDATE retail_compute_segments SET state='settled',deterministic_ms=%s,settled_at=%s WHERE segment_key=%s",(ms,time.time(),key), write=True)
    return contribution


def task_totals(store,task_id):
    row=store._execute_native("SELECT credits,usd FROM retail_totals WHERE scope='task' AND ref=%s AND n>0",(task_id,)).fetchone()
    return {'price':float(round(row['credits'],6)),'price_usd':float(round(row['usd'],6))} if row else None


def child_task_cost(store,task_id):
    """Read-only child accounting, including before the first debit lands.

    No settled rows is not proof of no cost. Pending/uncertain operations retain
    unknown totals while any confirmed contributions remain visible as partial.
    A legacy document-accounted session returns None to keep its own reader.
    """
    row=store._execute_native("""SELECT
        EXISTS(SELECT 1 FROM decision_reviews WHERE task_id=%s AND
            (revision='child-operation-v1' OR (revision='manifest-v1' AND wait_id LIKE 'child-generator:%%'))) AS child,
        EXISTS(SELECT 1 FROM worker_jobs WHERE task_id=%s AND state IN ('pending','running','paused')) AS active,
        EXISTS(SELECT 1 FROM provider_attempts p JOIN worker_jobs w USING(job_id)
            WHERE w.task_id=%s AND p.state IN ('reserved','dispatched','uncertain','held')) AS provider_pending,
        EXISTS(SELECT 1 FROM retail_compute_segments r JOIN worker_jobs w USING(job_id)
            WHERE w.task_id=%s AND r.state<>'settled') AS compute_pending,
        (SELECT credits FROM retail_totals WHERE scope='task' AND ref=%s AND n>0) AS credits,
        (SELECT usd FROM retail_totals WHERE scope='task' AND ref=%s AND n>0) AS usd
        FROM tasks t WHERE t.task_id=%s AND t.deleted_at IS NULL
        """,(task_id,)*7).fetchone()
    if row is None:raise KeyError('owned task not found')
    if not row['child']:return None
    complete=not (row['active'] or row['provider_pending'] or row['compute_pending'])
    evidence={'scope':'child ledger','complete':complete,
        'active_worker':row['active'],'provider_pending':row['provider_pending'],'compute_pending':row['compute_pending']}
    # Confirmed posted contributions are a lower bound while work is unsettled.
    return {'price':float(round(row['credits'],6)) if row['credits'] is not None else 0.0 if complete else None,
        'price_usd':float(round(row['usd'],6)) if row['usd'] is not None else 0.0 if complete else None,
        'evidence':evidence}


def task_phase_totals(store,task_id):
    """Credits per phase for one task, largest first. Credits only: no price, vendor cost or rate leaves this function."""
    rows=store._execute_native("SELECT phase_key,credits FROM retail_totals WHERE scope='task_phase' AND ref=%s AND n>0",(task_id,)).fetchall()
    by={r['phase_key']:r['credits'] for r in rows}
    return [{'phase':k,'credits':float(round(v,6))} for k,v in sorted(by.items(),key=lambda kv:(-kv[1],kv[0]))]


def _job_evidence(store,job_id):
    row=store._execute_native("SELECT count(*) AS n, COALESCE(sum(CASE WHEN state IN ('reserved','dispatched','uncertain','held') THEN 1 ELSE 0 END),0) AS nonterminal FROM provider_attempts WHERE job_id=%s",(job_id,)).fetchone()
    tokens=store._execute_native("SELECT count(*) AS n FROM retail_contributions WHERE job_id=%s AND kind='tokens'",(job_id,)).fetchone()['n']
    return int(row['n']),int(tokens),int(row['nonterminal'])


def _close_without_charge(store,segment,action,attempts):
    store._execute_native("UPDATE retail_compute_segments SET state='settled',deterministic_ms=%s,settled_at=%s WHERE segment_key=%s",(Decimal(0),time.time(),segment['segment_key']), write=True)
    store.audit(current_scope().owner_id,action,None,{'segment_key':str(segment['segment_key']),'job_id':str(segment['job_id']),'phase':segment['phase_key'],
        'previous_state':segment['state'],'provider_attempts':attempts,'charged':False})


def close_failed_compute(store,key):
    """A job failed after its compute segment began. With no provider call and no token charge there is nothing to price, so close the
    segment without a charge (audited). Otherwise keep it unresolved for review, as before."""
    owner_lock(store)
    segment=store._execute_native('SELECT * FROM retail_compute_segments WHERE segment_key=%s FOR UPDATE',(key,)).fetchone()
    if not segment:return 'missing'
    if segment['state']=='settled':return 'settled'
    attempts,tokens,_=_job_evidence(store,segment['job_id'])
    if close_decision(attempts,tokens)=='void':
        _close_without_charge(store,segment,'compute.segment_closed_no_provider_call',attempts)
        return 'closed'
    store._execute_native("UPDATE retail_compute_segments SET state='unresolved' WHERE segment_key=%s",(key,), write=True)
    return 'held'


def release_compute(store,owner_id,confirm):
    """The owner's own explicit release of segments that block new work. Only segments whose job is not running and never reached a
    provider are released, each with an audit record; anything else is listed and left alone. confirm=False only reports."""
    if confirm:owner_lock(store)
    rows=store._execute_native("SELECT * FROM retail_compute_segments WHERE owner_id=%s AND state<>'settled' ORDER BY started_at"+(' FOR UPDATE' if confirm else ''),(owner_id,)).fetchall()
    closable=[];kept=[]
    for row in rows:
        job=store._execute_native('SELECT state FROM worker_jobs WHERE job_id=%s',(row['job_id'],)).fetchone()
        job_state=job['state'] if job else None
        attempts,tokens,nonterminal=_job_evidence(store,row['job_id'])
        item={'segment':str(row['segment_key'])[:8],'job':str(row['job_id'])[:8],'state':row['state'],'provider_calls':attempts}
        decision=release_decision(row['state'],job_state,nonterminal,attempts,tokens)
        if decision:closable.append((row,item,attempts,decision))
        else:kept.append({**item,'reason':'its job is still running' if job_state=='running'
                else 'a provider call is still in flight; needs review' if nonterminal
                else 'its job reached a provider before it settled; needs review'})
    if confirm:
        for row,item,attempts,decision in closable:
            action='compute.segment_released_by_owner' if decision=='void' else 'compute.segment_reconciled_no_charge'
            _close_without_charge(store,row,action,attempts)
    return {'releasable':[i for _,i,_,_ in closable],'kept':kept,'released':len(closable) if confirm else 0}
