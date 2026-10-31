"""Operator-only atomic legacy-cap transition. Never used from HTTP routes."""
import hashlib
from .attempt_ledger import AdmissionDenied
from .launch_admission_policy import validate_owner_limits

SCALE=10**12

def transition(conn,email,*,apply=False):
    """Caller owns transaction. Apply is explicitly selected only after reviewed preview."""
    conn.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    row=conn.execute('SELECT id FROM auth.users WHERE lower(email)=lower(%s)',(email,)).fetchone()
    if not row:raise AdmissionDenied('exact signed-up owner email required')
    owner=row[0]
    conn.execute('SELECT pg_advisory_xact_lock(%s)',(int.from_bytes(hashlib.sha256(str(owner).encode()).digest()[:8],'big',signed=True),))
    names=('config_version','period_seconds','expense_limit','retail_limit','attempt_expense_max','attempt_retail_max','rate_max','concurrency_max')
    row=conn.execute('SELECT '+','.join(names)+' FROM admission_limits WHERE owner_id=%s FOR UPDATE',(owner,)).fetchone()
    if not row:raise AdmissionDenied('existing reviewed admission row required')
    old=dict(zip(names,row))
    existing=conn.execute('SELECT limit_units,units_per_usd FROM own_test_expense_profiles WHERE owner_id=%s',(owner,)).fetchone()
    done=old['expense_limit'] is None and existing==(2*SCALE,SCALE)
    if not done and (old['expense_limit']!=2*SCALE or old['period_seconds']!=86400):
        raise AdmissionDenied('only exact legacy $2/86400 cap may be replaced')
    validate_owner_limits(old|{'expense_limit':None})
    if existing and existing!=(2*SCALE,SCALE):raise AdmissionDenied('existing own-test profile conflicts')
    attempt=conn.execute("SELECT a.attempt_id,a.job_id,j.task_id,a.state FROM provider_attempts a JOIN worker_jobs j USING(job_id) WHERE a.owner_id=%s AND a.state IN ('reserved','dispatched','uncertain') ORDER BY a.created_at,a.attempt_id LIMIT 1",(owner,)).fetchone()
    if attempt:
        raise AdmissionDenied(f'owner attempts must be reconciled before transition: task={attempt[2]} job={attempt[1]} attempt={attempt[0]} state={attempt[3]}; confirm provider outcome before settling or cancelling; do not erase accounting')
    job=conn.execute("SELECT j.task_id,j.job_id,j.state FROM worker_jobs j JOIN tasks t ON t.task_id=j.task_id AND t.owner_id=j.owner_id WHERE j.owner_id=%s AND t.deleted_at IS NULL AND j.state IN ('pending','running') ORDER BY j.created_at,j.job_id LIMIT 1",(owner,)).fetchone()
    if job:
        raise AdmissionDenied(f'stop and reconcile owner work before transition: task={job[0]} job={job[1]} state={job[2]}; Stop this task and wait for confirmed worker quiescence, then retry; a stale job requires reviewed recovery, not row deletion')
    compute=conn.execute("SELECT s.segment_key,s.job_id,j.task_id,s.state FROM retail_compute_segments s JOIN worker_jobs j USING(job_id) WHERE s.owner_id=%s AND s.state IN ('pending','unresolved') ORDER BY s.started_at,s.segment_key LIMIT 1",(owner,)).fetchone()
    if compute:
        raise AdmissionDenied(f'owner compute accounting must be reconciled before transition: task={compute[2]} job={compute[1]} segment={compute[0]} state={compute[3]}; recover measured compute accounting before retrying; do not guess duration or delete the segment')
    # Do not erase history or guess the unit scale of legacy charges.
    if conn.execute("SELECT 1 FROM provider_attempts WHERE owner_id=%s AND state='settled' AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>extract(epoch FROM clock_timestamp()) OR (settled_at>extract(epoch FROM clock_timestamp())-86400 AND (expense_actual IS NULL OR quote->>'expense_units_per_usd' IS DISTINCT FROM %s))) LIMIT 1",(owner,str(SCALE))).fetchone():
        raise AdmissionDenied('recent provider expense history needs explicit scale reconciliation')
    result={'owner':str(owner),'old_expense_limit':old['expense_limit'],'new_expense_limit':None,
        'profile':'own-test $2 confirmed DeepSeek expense / rolling 24h','units_per_usd':SCALE,'already_applied':done}
    if apply and not done:
        conn.execute('INSERT INTO own_test_expense_profiles VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id) DO NOTHING',(owner,'own-test-2usd-rolling24h-v1',2*SCALE,SCALE))
        conn.execute('UPDATE admission_limits SET expense_limit=NULL,config_version=%s WHERE owner_id=%s',('own-test-2usd-rolling24h-v1',owner))
    return result
