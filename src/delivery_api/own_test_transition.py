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
    if conn.execute("SELECT 1 FROM provider_attempts WHERE owner_id=%s AND state IN ('reserved','dispatched','uncertain') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('owner attempts must be reconciled before transition')
    if conn.execute("SELECT 1 FROM worker_jobs WHERE owner_id=%s AND state IN ('pending','running') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('stop and reconcile owner work before transition')
    if conn.execute("SELECT 1 FROM retail_compute_segments WHERE owner_id=%s AND state IN ('pending','unresolved') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('owner compute accounting must be reconciled before transition')
    # Do not erase history or guess the unit scale of legacy charges.
    if conn.execute("SELECT 1 FROM provider_attempts WHERE owner_id=%s AND state='settled' AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>extract(epoch FROM clock_timestamp()) OR (settled_at>extract(epoch FROM clock_timestamp())-86400 AND (expense_actual IS NULL OR quote->>'expense_units_per_usd' IS DISTINCT FROM %s))) LIMIT 1",(owner,str(SCALE))).fetchone():
        raise AdmissionDenied('recent provider expense history needs explicit scale reconciliation')
    result={'owner':str(owner),'old_expense_limit':old['expense_limit'],'new_expense_limit':None,
        'profile':'own-test $2 confirmed DeepSeek expense / rolling 24h','units_per_usd':SCALE,'already_applied':done}
    if apply and not done:
        conn.execute('INSERT INTO own_test_expense_profiles VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id) DO NOTHING',(owner,'own-test-2usd-rolling24h-v1',2*SCALE,SCALE))
        conn.execute('UPDATE admission_limits SET expense_limit=NULL,config_version=%s WHERE owner_id=%s',('own-test-2usd-rolling24h-v1',owner))
    return result
