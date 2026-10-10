"""Once-only exact retail contributions, atomically posted under the owner lock."""
import hashlib,json,time
from decimal import Decimal
from .retail_pricing import RetailRates, UNITS_PER_CREDIT, decimal_value
from .tenancy import current_scope
from .attempt_ledger import AdmissionDenied
from .prepaid import owner_lock


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
    old=store._db.execute('SELECT * FROM retail_contributions WHERE contribution_key=?',(key,)).fetchone()
    if old:
        if old['fingerprint']!=fingerprint:raise AdmissionDenied('conflicting retail contribution replay')
        return dict(old)
    account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(current_scope().owner_id,)).fetchone()
    if not account or account['units_per_credit']!=UNITS_PER_CREDIT:
        raise AdmissionDenied('exact prepaid scale migration required')
    units,remainder=rates.split_units(input_tokens+output_tokens,ms,account['fractional_debit_units'])
    if units>2**63-1 or account['balance_units']-units<-(2**63):
        raise AdmissionDenied('retail amount outside account integer range')
    exact=rates.credits(input_tokens+output_tokens,ms)*UNITS_PER_CREDIT
    now=time.time()
    store._db.execute('INSERT INTO retail_contributions VALUES (?,?,?,?,?,?,?,?,?::jsonb,?,?,?,?,?)',
        (key,current_scope().owner_id,job_id,phase,kind,input_tokens,output_tokens,ms,json.dumps(rates.snapshot()),exact,units,UNITS_PER_CREDIT,fingerprint,now))
    store._db.execute('UPDATE credit_accounts SET balance_units=balance_units-?,fractional_debit_units=?,updated_at=? WHERE owner_id=?',
        (units,remainder,now,current_scope().owner_id))
    return dict(store._db.execute('SELECT * FROM retail_contributions WHERE contribution_key=?',(key,)).fetchone())


def phase_totals(store,job_id):
    """Display reads the debit evidence; it does not charge phases again."""
    rows=store._db.execute('SELECT * FROM retail_contributions WHERE job_id=? ORDER BY contribution_key',(job_id,)).fetchall()
    totals={}
    for r in rows:
        rates=rates_from_snapshot(r['rates']); phase=totals.setdefault(r['phase_key'],{'credits':Decimal(0),'usd':Decimal(0),'posted_units':0})
        credits=r['exact_units']/UNITS_PER_CREDIT
        phase['credits']+=credits;phase['usd']+=credits*rates.usd_per_credit;phase['posted_units']+=r['posted_units']
    return totals


def migrate_account(connection, owner_id, *, now):
    """Explicit private operator connection, never invoked during admission.

    Preserve immutable purchase/quote/charge evidence in its recorded scale.
    A pending item must be reconciled before changing the active account scale.
    """
    with connection.transaction():
        account=connection.execute('SELECT * FROM public.credit_accounts WHERE owner_id=%s FOR UPDATE',(owner_id,)).fetchone()
        if not account:raise AdmissionDenied('prepaid account missing')
        if account['units_per_credit']==UNITS_PER_CREDIT:return False
        if account['units_per_credit']!=1000:raise AdmissionDenied('unsupported historical prepaid scale')
        pending=connection.execute("SELECT EXISTS(SELECT 1 FROM public.provider_attempts WHERE owner_id=%s AND state IN ('reserved','dispatched','uncertain')) OR EXISTS(SELECT 1 FROM public.credit_charges WHERE owner_id=%s AND state='pending') OR EXISTS(SELECT 1 FROM public.payment_purchases WHERE owner_id=%s AND NOT credit_posted)",(owner_id,owner_id,owner_id)).fetchone()
        if next(iter(pending.values())):raise AdmissionDenied('pending provider or payment evidence; scale migration held')
        balance=account['balance_units']*(UNITS_PER_CREDIT//1000)
        if not -(2**63)<=balance<=2**63-1:raise AdmissionDenied('migrated balance outside integer range')
        if account['fractional_debit_units']!=0:raise AdmissionDenied('legacy fractional remainder needs reconciliation')
        connection.execute('INSERT INTO public.retail_scale_migrations VALUES(%s,%s,%s,%s,%s,%s)',(owner_id,1000,account['balance_units'],UNITS_PER_CREDIT,balance,now))
        connection.execute('UPDATE public.credit_accounts SET balance_units=%s,units_per_credit=%s,updated_at=%s WHERE owner_id=%s',(balance,UNITS_PER_CREDIT,now,owner_id))
        return True


def begin_compute(store,key,job_id,phase,rates):
    rates=rates_from_snapshot(rates).snapshot();owner_lock(store)
    job=store.worker_job(job_id)
    if not job or job['state']!='running':raise AdmissionDenied('running compute job required')
    if store._db.execute('SELECT 1 FROM retail_compute_segments WHERE segment_key=?',(key,)).fetchone():
        raise AdmissionDenied('compute segment already begun; explicit recovery required')
    store._db.execute("INSERT INTO retail_compute_segments(segment_key,owner_id,job_id,phase_key,rates,state,started_at) VALUES (?,?,?,?,?::jsonb,'pending',?)",(key,current_scope().owner_id,job_id,phase,json.dumps(rates),time.time()))


def finish_compute(store,key,deterministic_ms):
    owner_lock(store)
    segment=store._db.execute('SELECT * FROM retail_compute_segments WHERE segment_key=? FOR UPDATE',(key,)).fetchone()
    if not segment:raise AdmissionDenied('compute segment missing')
    if deterministic_ms is None:
        if segment['state']!='settled':store._db.execute("UPDATE retail_compute_segments SET state='unresolved' WHERE segment_key=?",(key,))
        return None
    ms=decimal_value(deterministic_ms,'deterministic_ms')
    if segment['state']=='settled':
        if ms!=segment['deterministic_ms']:raise AdmissionDenied('conflicting compute replay')
        return dict(segment)
    if segment['state']=='unresolved':raise AdmissionDenied('unresolved compute needs explicit reconciliation')
    contribution=post(store,key='compute:'+key,job_id=segment['job_id'],phase=segment['phase_key'],kind='compute',deterministic_ms=ms,rates=segment['rates'])
    store._db.execute("UPDATE retail_compute_segments SET state='settled',deterministic_ms=?,settled_at=? WHERE segment_key=?",(ms,time.time(),key))
    return contribution


def task_totals(store,task_id):
    rows=store._db.execute('SELECT r.* FROM retail_contributions r JOIN worker_jobs w ON r.job_id=w.job_id WHERE w.task_id=?',(task_id,)).fetchall()
    if not rows:return None
    credits=Decimal(0);usd=Decimal(0)
    for row in rows:
        value=row['exact_units']/UNITS_PER_CREDIT
        credits+=value;usd+=value*rates_from_snapshot(row['rates']).usd_per_credit
    return {'price':float(round(credits,6)),'price_usd':float(round(usd,6))}
