"""Private exact-run accounting. Caller supplies verified scope and reviewed plan.

No public route, source fetch, model call, job re-dispatch or authentication mint.
"""
import json,time
from decimal import Decimal
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.postgres_store import _transactional
from delivery_api.tenancy import current_scope
from delivery_api.prepaid import owner_lock,debit
from delivery_api.retail_ledger import post
from recovery_plan import accounting_preview,digest

ACTION='item6.provider.reconciled'

@_transactional
def reconcile(store,attempt_id,*,review_sha256,original_dump_sha256,source_verification):
    if len(original_dump_sha256)!=64 or any(c not in '0123456789abcdef' for c in original_dump_sha256):
        raise AdmissionDenied('original archive hash required')
    if not callable(source_verification):raise AdmissionDenied('historic source verifier callback required')
    store._db.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    owner_lock(store)
    r=store._db.execute('SELECT * FROM provider_attempts WHERE attempt_id=? FOR UPDATE',(attempt_id,)).fetchone()
    if not r:raise AdmissionDenied('owned attempt missing')
    job=store.worker_job(r['job_id']);scope=current_scope()
    if not job or str(job['owner_id'])!=scope.owner_id or str(job['auth_session_id'])!=scope.session_id or job['state']!='failed':
        raise AdmissionDenied('original scoped failed job required')
    old=store._db.execute("SELECT detail FROM audit_log WHERE task_id=? AND action=? AND detail::jsonb->>'attempt_id'=?",(job['task_id'],ACTION,attempt_id)).fetchall()
    if old:
        if len(old)!=1:raise AdmissionDenied('ambiguous reconciliation history')
        proof=json.loads(old[0]['detail'])
        if proof['review_sha256']!=review_sha256 or proof['original_dump_sha256']!=original_dump_sha256 or r['state']!='settled':
            raise AdmissionDenied('conflicting reconciliation replay')
        return {'replayed':True,'attempt_id':attempt_id,'state':'settled','job_state':job['state']}
    account=store._db.execute('SELECT * FROM credit_accounts WHERE owner_id=? FOR UPDATE',(scope.owner_id,)).fetchone()
    plan=accounting_preview(dict(r),job,dict(account))
    if source_verification(r['quote']['provider_evidence']['snapshot']) is not True:
        raise AdmissionDenied('historic source verification did not pass')
    if plan['review_sha256']!=review_sha256:raise AdmissionDenied('changed or unreviewed accounting plan')
    if store._db.execute('SELECT 1 FROM retail_contributions WHERE contribution_key=?',('attempt:'+attempt_id,)).fetchone():
        raise AdmissionDenied('unexpected prior token contribution')
    charge=store._db.execute('SELECT * FROM credit_charges WHERE attempt_id=? FOR UPDATE',(attempt_id,)).fetchone()
    if not charge or charge['state']!='pending':raise AdmissionDenied('original pending prepaid charge required')
    contribution=post(store,key='attempt:'+attempt_id,job_id=job['job_id'],phase=r['quote'].get('retail_phase','intent'),kind='tokens',
        input_tokens=plan['usage']['input_tokens'],output_tokens=plan['usage']['output_tokens'],rates=r['quote']['retail_formula'])
    if contribution['posted_units']!=plan['retail_posted_units']:raise AdmissionDenied('preview/posting mismatch')
    debit(store,dict(r),plan['retail_posted_units'],already_posted=True)
    now=time.time()
    store._db.execute("UPDATE provider_attempts SET state='settled',expense_actual=?,retail_actual=?,usage=?::jsonb,billed_rate_evidence=?::jsonb,updated_at=?,settled_at=? WHERE attempt_id=? AND state='uncertain'",
       (plan['expense_actual'],plan['retail_posted_units'],json.dumps(plan['usage']),json.dumps(plan['billed_rate_evidence']),now,now,attempt_id))
    store.confirmed_expense_admission(job['job_id'],plan['expense_units_per_usd'])
    store.audit(scope.owner_id,ACTION,job['task_id'],{'attempt_id':attempt_id,'review_sha256':review_sha256,'original_dump_sha256':original_dump_sha256,'source_verification':'matched-historic-snapshot','plan':plan,'failed_job_unchanged':True})
    return {'replayed':False,'attempt_id':attempt_id,'state':'settled','job_state':job['state'],'plan':plan}

@_transactional
def waive_unknown_compute(store,key,*,original_dump_sha256,authorization_ref):
    if not authorization_ref or len(original_dump_sha256)!=64:raise AdmissionDenied('waiver authority and archive evidence required')
    owner_lock(store)
    segment=store._db.execute('SELECT * FROM retail_compute_segments WHERE segment_key=? FOR UPDATE',(key,)).fetchone()
    if not segment:raise AdmissionDenied('owned compute segment missing')
    job=store.worker_job(segment['job_id']);scope=current_scope()
    if not job or job['state']!='failed' or str(job['auth_session_id'])!=scope.session_id:raise AdmissionDenied('original scoped failed job required')
    if segment['state']=='waived':
        entries=store._db.execute("SELECT detail FROM audit_log WHERE task_id=? AND action='item6.compute.failure_waived' AND detail::jsonb->>'segment_key'=?",(job['task_id'],key)).fetchall()
        if len(entries)!=1:raise AdmissionDenied('waiver audit missing or ambiguous')
        evidence=json.loads(entries[0]['detail'])
        if evidence['authorization_ref']!=authorization_ref or evidence['original_dump_sha256']!=original_dump_sha256:
            raise AdmissionDenied('conflicting compute waiver replay')
        return {'state':'waived','replayed':True,'billed_units':0,'measurement':'unknown'}
    if segment['state']!='unresolved' or segment['deterministic_ms'] is not None:
        raise AdmissionDenied('only unknown unresolved compute may be waived')
    if store._db.execute("SELECT 1 FROM retail_contributions WHERE job_id=? AND kind='compute'",(job['job_id'],)).fetchone():
        raise AdmissionDenied('existing compute billing needs separate review')
    store._db.execute("UPDATE retail_compute_segments SET state='waived' WHERE segment_key=? AND state='unresolved'",(key,))
    store.audit(scope.owner_id,'item6.compute.failure_waived',job['task_id'],{'segment_key':key,'measurement':'unknown','disposition':'waived','billed_units':0,'failed_job_unchanged':True,'original_dump_sha256':original_dump_sha256,'authorization_ref':authorization_ref})
    return {'state':'waived','replayed':False,'billed_units':0,'measurement':'unknown'}
