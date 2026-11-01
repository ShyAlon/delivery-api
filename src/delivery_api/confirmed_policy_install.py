"""Explicit owner-run policy installation; no HTTP/runtime call site.

Caller owns transaction and chooses apply only after reviewed preview. No
profile, cap, credit, accounting or execution setting is changed here.
"""
import json

from .attempt_ledger import AdmissionDenied
from .paid_child_preflight import SCALE


def plan(conn,*,apply=False):
    for table in ('expense_guard_policy','worker_jobs','provider_attempts','retail_compute_segments'):
        visible=conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT') AND NOT row_security_active(%s::regclass)",('public.'+table,'public.'+table)).fetchone()
        if not visible or not visible[0]:raise AdmissionDenied('policy installation inspection unavailable; do not bypass hidden accounting')
    conn.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    row=conn.execute('SELECT policy_version,session_units,per_registered_user_units,units_per_usd,max_alert_attempts FROM expense_guard_policy WHERE singleton FOR UPDATE').fetchone()
    expected=('confirmed-3usd-session-platform-v1',3*SCALE,3*SCALE,SCALE,3)
    if row is not None and tuple(row[1:])!=expected[1:]:raise AdmissionDenied('existing confirmed expense policy differs; explicit policy review required, never overwrite')
    blockers=[]
    checks=(
        ('worker_jobs','job_id',"state IN ('pending','running')",(), 'all work must be quiescent'),
        ('provider_attempts','attempt_id',"state IN ('reserved','dispatched','uncertain')",(), 'provider accounting must be reconciled'),
        ('retail_compute_segments','segment_key',"state IN ('pending','unresolved')",(), 'compute accounting must be reconciled'),
        ('provider_attempts','attempt_id',"state='settled' AND (expense_actual IS NULL OR settled_at IS NULL OR settled_at>extract(epoch FROM clock_timestamp()) OR (quote->>'provider'='deepseek' AND quote->>'expense_units_per_usd' IS DISTINCT FROM %s))",(str(SCALE),), 'provider history requires scale/outcome review; never rewrite history'),
    )
    for table,key,predicate,args,reason in checks:
        # Fixed internal identifiers/predicates only. Bound output, never quotes,
        # usage payloads, task titles, keys or connection details.
        rows=conn.execute(f'SELECT {key},owner_id,state,count(*) OVER() FROM {table} WHERE {predicate} ORDER BY {key} LIMIT 20',args).fetchall()
        if rows:
            blockers.append({'table':table,'reason':reason,'count':rows[0][3],
                'records':[{'id':r[0],'owner_id':str(r[1]),'state':r[2]} for r in rows],
                'truncated':rows[0][3]>len(rows)})
    if blockers:
        raise AdmissionDenied('policy installation blocked: '+json.dumps(blockers,sort_keys=True))
    if apply and row is None:
        conn.execute('INSERT INTO expense_guard_policy VALUES(true,%s,%s,%s,%s,%s)',expected)
    return {'already_installed':row is not None,'policy_version':row[0] if row else expected[0],
        'session_usd':3,'platform_usd_per_registered_user':3,'expense_units_per_usd':SCALE,
        'max_alert_attempts':3,'activation_enabled':False,'changes_only':'confirmed expense policy row'}
