"""Owner-run read-only paid-child readiness proof, never an activation switch.

Use an authorized inspecting connection with RLS visibility. Hidden policy rows
are an inspection failure, not proof policy is missing. No mutation/repair.
"""
from .attempt_ledger import AdmissionDenied
from .launch_admission_policy import validate_owner_limits

SCALE=10**12


def inspect(conn,email,*,require_own_test=True):
    # The caller must supply a fresh connection: these are the first statements.
    conn.execute('SET TRANSACTION READ ONLY')
    conn.execute("SET LOCAL statement_timeout='5s'")
    tables=('admission_limits','own_test_expense_profiles','expense_guard_policy','provider_attempts','retail_compute_segments','credit_accounts','worker_jobs')
    for table in tables:
        row=conn.execute('SELECT has_table_privilege(current_user,%s,\'SELECT\') AND NOT row_security_active(%s::regclass)',('public.'+table,'public.'+table)).fetchone()
        if not row or not row[0]:raise AdmissionDenied('paid-child policy inspection unavailable')
    owner=conn.execute('SELECT id FROM auth.users WHERE lower(email)=lower(%s)',(email,)).fetchone()
    if not owner:raise AdmissionDenied('exact signed-up owner required')
    owner=owner[0]
    names=('config_version','period_seconds','expense_limit','retail_limit','attempt_expense_max','attempt_retail_max','rate_max','concurrency_max')
    row=conn.execute('SELECT '+','.join(names)+' FROM admission_limits WHERE owner_id=%s',(owner,)).fetchone()
    validate_owner_limits(dict(zip(names,row)) if row else None)
    profile=conn.execute('SELECT limit_units,units_per_usd FROM own_test_expense_profiles WHERE owner_id=%s',(owner,)).fetchone()
    if (require_own_test and profile!=(2*SCALE,SCALE)) or (profile is not None and profile!=(2*SCALE,SCALE)):
        raise AdmissionDenied('own-test profile missing or inconsistent; use explicit reviewed transition, never clear caps here')
    policy=conn.execute('SELECT session_units,per_registered_user_units,units_per_usd,max_alert_attempts FROM expense_guard_policy WHERE singleton').fetchone()
    if not policy or tuple(policy[:3])!=(3*SCALE,3*SCALE,SCALE) or policy[3]<1:
        raise AdmissionDenied('confirmed $3 session/platform policy missing or inconsistent')
    if conn.execute("SELECT 1 FROM provider_attempts WHERE state IN ('dispatched','uncertain') OR (owner_id=%s AND state='reserved') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('provider accounting must be reconciled before activation')
    if conn.execute("SELECT 1 FROM retail_compute_segments WHERE owner_id=%s AND state IN ('pending','unresolved') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('compute accounting must be reconciled before activation')
    if conn.execute("SELECT 1 FROM worker_jobs WHERE owner_id=%s AND state IN ('pending','running') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('owner work must be quiescent before activation')
    if conn.execute("SELECT 1 FROM provider_attempts WHERE state='settled' AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>extract(epoch FROM clock_timestamp()) OR expense_actual IS NULL OR quote->>'expense_units_per_usd' IS DISTINCT FROM %s) LIMIT 1",(str(SCALE),)).fetchone():
        raise AdmissionDenied('provider expense history needs explicit scale/outcome review')
    balance=conn.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s',(owner,)).fetchone()
    if not balance or balance[0]<=0 or balance[1]!=10**9:raise AdmissionDenied('positive exact-scale prepaid balance required')
    return {'database_policy_ready':True,'own_test_profile_present':profile is not None,
        'session_usd':3,'platform_usd_per_registered_user':3,'owner_concurrent_calls':3,
        'activation_enabled':False,'unverified':['running build tuple and schema readiness','reviewed vendor/model and provider balance','alert route/transport and delivery','whole-workflow installed-runtime acceptance','explicit activation/spend approval']}
