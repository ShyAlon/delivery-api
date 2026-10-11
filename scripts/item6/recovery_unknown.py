"""Private unknown-usage write-off, not invented settlement or free inference."""
import json,time
from delivery_api.postgres_store import _transactional
from delivery_api.prepaid import owner_lock
from delivery_api.tenancy import current_scope
from delivery_api.attempt_ledger import AdmissionDenied
ATTEMPT='d67d326e4b3a4a7e957b88cc53e43019'
JOB='2068b70d89a9f5d94296309707176822'
AUTHORITY='wamid.HBgMOTcyNTQ1Njc4MzMwFQIAEhgUM0I3REU1N0EzRTJFNkI1RUExMjUA'
ARCHIVE='134298e599e73349288a153e2acc20f98f5f76d2015ddc0f73c58a931f8ddc52'


def validate_architecture_failure(store):
    """Exact isolated stopped ledger before either write-off or sandbox top-up."""
    with store._postgres.transaction() as c:
        jobs=c.execute('SELECT job_id,state FROM worker_jobs WHERE task_id=%s',('c11813e4a4b7',)).fetchall()
        expected={'edad945ddadfa63b5bd73d5546a5bf69':'failed','9e04190ef41fac9f64e5f75b96e60b77':'failed',JOB:'failed','53467a7e6d7551d4d56c0a6ff62abff7':'done'}
        if {r['job_id']:r['state'] for r in jobs}!=expected:raise AdmissionDenied('exact stopped generator jobs required')
        rows=c.execute('SELECT * FROM provider_attempts').fetchall()
        if len(rows)!=120 or sum(r['state']=='settled' for r in rows)!=119 or any(r.get('input_estimate_overrun') for r in rows):raise AdmissionDenied('exact119settled + unknown attempt required')
        held=[r for r in rows if r['state']!='settled']
        if len(held)!=1 or held[0]['attempt_id']!=ATTEMPT or held[0]['state']!='uncertain' or held[0]['expense_reserved']!=21394800000 or any(held[0].get(k) is not None for k in ('usage','raw_usage','expense_actual','retail_actual','settled_at')):raise AdmissionDenied('exact wholly unknown attempt required')
        settled=sum(r['expense_actual'] for r in rows if r['state']=='settled' and r['job_id']!='edad945ddadfa63b5bd73d5546a5bf69')
        if settled!=20266290000:raise AdmissionDenied('cumulative provider expense changed')
        account=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts').fetchone()
        if account['balance_units']!=362835621 or account['units_per_credit']!=10**9:raise AdmissionDenied('exact stopped existing credit account required')
        states={r['job_id']:r['state'] for r in c.execute('SELECT job_id,state FROM retail_compute_segments').fetchall()}
        if states!={'edad945ddadfa63b5bd73d5546a5bf69':'waived','9e04190ef41fac9f64e5f75b96e60b77':'waived',JOB:'unresolved','53467a7e6d7551d4d56c0a6ff62abff7':'settled'}:raise AdmissionDenied('exact three failures and completed interview accounting required')
    return {'settled_attempts':119,'unknown_attempt_id':ATTEMPT,'balance_units':362835621,'cumulative_provider_confirmed_units':settled,'conservative_provider_reserved_units':21394800000,'remaining_original_cap_units':1000000000000-settled-21394800000}

@_transactional
def waive_unknown_provider(store,*,authorization_ref,archive_sha256):
    if authorization_ref!=AUTHORITY or archive_sha256!=ARCHIVE:raise AdmissionDenied('exact write-off authority/archive required')
    store._db.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)');owner_lock(store)
    r=store._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=? FOR UPDATE',(ATTEMPT,)).fetchone()
    job=store.worker_job(JOB);scope=current_scope()
    if not r or r['job_id']!=JOB or not job or job['state']!='failed' or str(job['auth_session_id'])!=scope.session_id:raise AdmissionDenied('exact scoped failed generator attempt required')
    action='item6.provider.unknown_failure_waived'
    previous=store._db.execute('SELECT detail FROM audit_log WHERE task_id=? AND action=?',(job['task_id'],action)).fetchall()
    if r['state']=='failure_waived':
        if len(previous)!=1:raise AdmissionDenied('ambiguous write-off replay')
        proof=json.loads(previous[0]['detail'])
        if proof['authorization_ref']!=authorization_ref or proof['archive_sha256']!=archive_sha256:raise AdmissionDenied('changed write-off replay')
        return proof|{'replayed':True}
    if r['state']!='uncertain' or previous or any(r.get(k) is not None for k in ('raw_usage','usage','raw_usage_received_at','expense_actual','retail_actual','settled_at')):raise AdmissionDenied('only exact wholly unknown evidence may be waived')
    if r['expense_reserved']!=21394800000:raise AdmissionDenied('conservative expense reservation changed')
    account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(scope.owner_id,)).fetchone()
    if account['balance_units']!=362835621:raise AdmissionDenied('reviewed balance changed')
    charge=store._db.execute('SELECT * FROM credit_charges WHERE attempt_id=? FOR UPDATE',(ATTEMPT,)).fetchone()
    if not charge or charge['state']!='pending' or charge['charged_units'] is not None:raise AdmissionDenied('exact unbilled pending charge required')
    if store._db.execute('SELECT 1 FROM retail_contributions WHERE contribution_key=?',('attempt:'+ATTEMPT,)).fetchone():raise AdmissionDenied('unexpected token contribution')
    store._db.execute("UPDATE provider_attempts SET state='failure_waived',updated_at=? WHERE attempt_id=?",(time.time(),ATTEMPT))
    store._db.execute("UPDATE credit_charges SET state='failure_waived' WHERE attempt_id=?",(ATTEMPT,))
    proof={'attempt_id':ATTEMPT,'failed_job':JOB,'archive_sha256':archive_sha256,'authorization_ref':authorization_ref,'usage':'unknown','retail_debit_units':0,'provider_expense_actual':None,'provider_debit_confirmed':False,'conservative_cap_consumption_units':21394800000,'balance_unchanged':True,'failed_job_unchanged':True}
    store.audit(scope.owner_id,action,job['task_id'],proof)
    return proof|{'replayed':False}
