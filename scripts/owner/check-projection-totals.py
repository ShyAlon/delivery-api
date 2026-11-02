#!/usr/bin/env python3
"""Owner-run read-only verification. Never initializes or refreshes lazy caches."""
import argparse,importlib.util,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def module(path,name):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

ADMISSION="""
WITH expected AS (
 SELECT t.owner_id,count(*) FILTER(WHERE a.input_estimate_overrun AND a.state<>'uncertain') overruns,
 count(*) FILTER(WHERE a.state IN ('reserved','dispatched')) held,
 count(*) FILTER(WHERE a.state='uncertain') uncertain,
 count(*) FILTER(WHERE a.created_at>t.as_of-t.rate_window_seconds) recent,
 COALESCE(sum(CASE WHEN a.state IN ('reserved','dispatched','uncertain','failure_waived') THEN a.expense_reserved WHEN a.state='settled' AND a.created_at>t.as_of-t.period_seconds THEN a.expense_actual ELSE 0 END),0) used_expense,
 COALESCE(sum(CASE WHEN a.state IN ('reserved','dispatched','uncertain','failure_waived') THEN a.retail_reserved WHEN a.state='settled' AND a.created_at>t.as_of-t.period_seconds THEN a.retail_actual ELSE 0 END),0) used_retail
 FROM admission_totals t LEFT JOIN provider_attempts a ON a.owner_id=t.owner_id GROUP BY t.owner_id
)
SELECT count(*) FROM admission_totals t JOIN expected e USING(owner_id)
WHERE (t.overruns,t.held,t.uncertain,t.recent,t.used_expense,t.used_retail) IS DISTINCT FROM
(e.overruns,e.held,e.uncertain,e.recent,e.used_expense,e.used_retail)
"""
RETAIL="""
WITH expected AS (
 SELECT r.owner_id,v.scope,v.ref,v.phase_key,count(*) n,sum(r.exact_units/1000000000) credits,
 sum(r.exact_units/1000000000*(r.rates->>'usd_per_credit')::numeric) usd,sum(r.posted_units) posted_units
 FROM retail_contributions r JOIN worker_jobs j USING(job_id)
 CROSS JOIN LATERAL (VALUES('task',j.task_id,''),('task_phase',j.task_id,r.phase_key),('job_phase',r.job_id,r.phase_key)) v(scope,ref,phase_key)
 GROUP BY r.owner_id,v.scope,v.ref,v.phase_key
), actual AS(SELECT * FROM retail_totals WHERE n>0)
SELECT count(*) FROM expected e FULL JOIN actual a USING(owner_id,scope,ref,phase_key)
WHERE (e.n,e.credits,e.usd,e.posted_units) IS DISTINCT FROM (a.n,a.credits,a.usd,a.posted_units)
"""
EXPENSE="""
WITH a AS(SELECT a.*,j.session_id,c.as_of FROM provider_attempts a JOIN worker_jobs j USING(job_id) CROSS JOIN expense_total_clock c),
parts AS (
 SELECT 'users' bucket,count(*)::numeric value FROM auth.users
 UNION ALL SELECT 'unknown',1 FROM a WHERE state='uncertain'
 UNION ALL SELECT 'unknown:'||owner_id,1 FROM a WHERE state='uncertain'
 UNION ALL SELECT 'session:'||owner_id||':'||session_id,COALESCE(expense_actual,0) FROM a WHERE state='settled'
 UNION ALL SELECT 'platform',COALESCE(expense_actual,0) FROM a WHERE state='settled' AND settled_at>as_of-86400 AND settled_at<=as_of
 UNION ALL SELECT 'bad:'||owner_id,1 FROM a WHERE state='settled' AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>as_of)
 UNION ALL SELECT 'own:'||owner_id,COALESCE(expense_actual,0) FROM a WHERE state='settled' AND quote->>'provider'='deepseek' AND settled_at>as_of-86400 AND settled_at<=as_of
 UNION ALL SELECT 'active:'||owner_id,1 FROM a WHERE state='settled' AND quote->>'provider'='deepseek' AND settled_at>as_of-86400 AND settled_at<=as_of
 UNION ALL SELECT 'valid:'||owner_id||':'||(quote->>'expense_units_per_usd'),1 FROM a WHERE state='settled' AND quote->>'provider'='deepseek' AND settled_at>as_of-86400 AND settled_at<=as_of AND expense_actual IS NOT NULL AND quote->>'expense_units_per_usd' IS NOT NULL
), expected AS(SELECT bucket,sum(value) value FROM parts GROUP BY bucket)
SELECT count(*) FROM expected e FULL JOIN expense_totals t USING(bucket) WHERE COALESCE(e.value,0)<>COALESCE(t.value,0)
"""

def check(c):
    required=('admission_totals','admission_total_entries','expense_totals','expense_total_clock','expense_total_entries','retail_totals','document_chunks','document_projections')
    missing=[name for name in required if not c.execute('SELECT to_regclass(%s) IS NOT NULL',('public.'+name,)).fetchone()[0]]
    result={'read_only':True,'tables':'PASS' if not missing else 'FAIL','missing_tables':missing}
    if missing:return result,1
    n=c.execute('SELECT count(*) FROM admission_totals').fetchone()[0]
    policies=c.execute('SELECT count(*) FROM admission_limits').fetchone()[0]
    result['admission']={'initialized_owners':n,'configured_owners':policies,'basis':'each stored as_of, not current clock','mismatches':c.execute(ADMISSION).fetchone()[0]}
    ready=bool(c.execute('SELECT count(*) FROM expense_total_clock').fetchone()[0])
    result['expense']={'initialized':ready,'basis':'stored global as_of, not current clock','mismatches':c.execute(EXPENSE).fetchone()[0] if ready else None}
    result['retail']={'mismatches':c.execute(RETAIL).fetchone()[0]}
    result['documents']='table existence only; JSONL/chunk content not audited by this totals check'
    failed=any(result[k]['mismatches'] for k in ('admission','expense','retail'))
    incomplete=not ready or n<policies
    result['status']='FAIL' if failed else 'INCOMPLETE: lazy caches not initialized; no writes performed' if incomplete else 'PASS'
    return result,1 if failed else 2 if incomplete else 0

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',required=True);a=p.parse_args()
    s=module(ROOT/'scripts/ops/settings.py','settings');values=s.load(ROOT,explicit=a.env_file)
    if not values.get('REQUI_ADMIN_DATABASE_URL'):raise ValueError('REQUI_ADMIN_DATABASE_URL missing; no prompt or changes')
    import psycopg
    with psycopg.connect(values['REQUI_ADMIN_DATABASE_URL'],connect_timeout=10,autocommit=True) as c:
        c.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            c.execute("SET LOCAL statement_timeout='30s'");c.execute("SET LOCAL lock_timeout='2s'")
            result,code=check(c)
        finally:c.execute('ROLLBACK')
    print(json.dumps(result,indent=2));print('No refresh functions, migrations, grants, settings saves or data writes executed. Exit 2 means lazy initialization remains unverified.')
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:raise SystemExit('CHECK FAILED: '+type(exc).__name__+'; no credential-bearing details printed, no writes performed')
