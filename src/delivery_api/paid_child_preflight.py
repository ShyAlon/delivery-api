"""Owner-run read-only paid-child readiness proof, never an activation switch.

Use an authorized inspecting connection with RLS visibility. Hidden policy rows
are an inspection failure, not proof policy is missing. No mutation/repair.
"""
from .attempt_ledger import AdmissionDenied
from .launch_admission_policy import validate_owner_limits

SCALE=10**12


def inspect(conn,email,*,require_own_test=True):
    # The caller must supply a fresh connection: these are the first statements.
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute("SET LOCAL statement_timeout='5s'")
    tables=('admission_limits','own_test_expense_profiles','expense_guard_policy','provider_attempts','retail_compute_segments','credit_accounts','worker_jobs','operator_expense_policy_head','operator_expense_policy_revisions')
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
    operator=conn.execute('SELECT r.limit_units,r.units_per_usd FROM operator_expense_policy_head h JOIN operator_expense_policy_revisions r USING(revision) WHERE h.singleton').fetchone()
    if not operator or operator[0]<=0 or operator[1]!=SCALE:
        raise AdmissionDenied('positive exact-scale operator expense policy required')
    if conn.execute("SELECT 1 FROM provider_attempts WHERE owner_id=%s AND state IN ('dispatched','reserved') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('owner provider calls must be quiescent before readiness review')
    warnings=[]
    unknown=conn.execute("SELECT count(*) FROM provider_attempts WHERE state='uncertain'").fetchone()[0]
    unresolved=conn.execute("SELECT count(*) FROM retail_compute_segments WHERE owner_id=%s AND state IN ('pending','unresolved')",(owner,)).fetchone()[0]
    if unknown: warnings.append({'uncertain_provider_attempts':unknown,'effect':'audit warning; unknown final bills can exceed a ceiling; never auto-replay'})
    if unresolved: warnings.append({'unresolved_owner_compute_segments':unresolved,'effect':'audit warning; not a provider confirmed-cost admission block'})
    if conn.execute("SELECT 1 FROM worker_jobs WHERE owner_id=%s AND state IN ('pending','running') LIMIT 1",(owner,)).fetchone():
        raise AdmissionDenied('owner work must be quiescent before readiness review')
    if conn.execute("SELECT 1 FROM provider_attempts WHERE state='settled' AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>extract(epoch FROM clock_timestamp()) OR expense_actual IS NULL OR quote->>'expense_units_per_usd' IS DISTINCT FROM %s) LIMIT 1",(str(SCALE),)).fetchone():
        raise AdmissionDenied('provider expense history needs explicit scale/outcome review')
    balance=conn.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s',(owner,)).fetchone()
    if not balance or balance[0]<=0 or balance[1]!=10**9:raise AdmissionDenied('positive exact-scale prepaid balance required')
    # Query authoritative settled rows, not lazy projections: refreshing those
    # would write and violate this inspector's read-only contract. One snapshot
    # time and the SQL guard's strict rolling boundary are shared by all sums.
    moment=conn.execute('SELECT extract(epoch FROM transaction_timestamp())').fetchone()[0]
    own=conn.execute("SELECT COALESCE(sum(expense_actual),0) FROM provider_attempts WHERE owner_id=%s AND state='settled' AND quote->>'provider'='deepseek' AND settled_at>%s-86400 AND settled_at<=%s",(owner,moment,moment)).fetchone()[0]
    platform=conn.execute("SELECT COALESCE(sum(expense_actual),0) FROM provider_attempts WHERE state='settled' AND settled_at>%s-86400 AND settled_at<=%s",(moment,moment)).fetchone()[0]
    users=conn.execute('SELECT count(*) FROM auth.users').fetchone()[0]
    sessions=conn.execute("SELECT j.session_id,COALESCE(sum(p.expense_actual),0) FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE p.owner_id=%s AND p.state='settled' GROUP BY j.session_id",(owner,)).fetchall()
    def guard(name,spent,limit):
        return {'guard':name,'spent_usd':float(spent/SCALE),'limit_usd':float(limit/SCALE),'remaining_usd':float(max(0,limit-spent)/SCALE),'blocked':spent>=limit}
    guards=[]
    if profile: guards.append(guard('own_test_rolling_24h',own,profile[0]))
    guards.append(guard('platform_rolling_24h',platform,policy[1]*users))
    # This ranking is for a NEW zero-spend session/generation only. Existing
    # session totals are separate; the operator guard uses outstanding worst-case
    # exposure, not confirmed costs, so it cannot be ranked as equal money.
    guards.append(guard('new_session',0,policy[0]))
    first=min(guards,key=lambda g:g['remaining_usd'])['guard']
    return {'database_policy_ready':not any(g['blocked'] for g in guards),'own_test_profile_present':profile is not None,
        'session_usd':3,'platform_usd_per_registered_user':3,'owner_concurrent_calls':3,
        'prepaid_credits':float(balance[0]/balance[1]),
        'credit_note':'Positive balance is not proof of full-run credit sufficiency; check the configured next-phase minimum (default 5 credits) and available balance before each phase.',
        'activation_enabled':False,'warnings':warnings,'confirmed_cost_guards':guards,
        'first_confirmed_guard_for_new_session':first,
        'existing_session_spend_usd':{str(s):float(v/SCALE) for s,v in sessions},
        'operator_task_generation_ceiling_usd':float(operator[0]/SCALE),
        'operator_guard_note':'Separate worst-case exposure check on every dispatch; not a replacement for confirmed-cost guards. Existing generation exposure is not inspected here.',
        'snapshot_note':'Read-only snapshot, not a reservation or spend approval; running calls finish and concurrent spending can change headroom.',
        'unverified':['running build tuple and schema readiness','reviewed vendor/model and provider balance','alert route/transport and delivery','whole-workflow installed-runtime acceptance','owner-started paid execution']}
