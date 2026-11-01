#!/usr/bin/env python3
"""Review/delete existing tasks for ONE authenticated owner. DRY by default.

Never truncates tables, releases holds, changes credits or deletes source files.
Existing Stop/provider reconciliation rules remain mandatory. Accounting and
immutable review evidence stay in Postgres. Run only after stopping producers.
"""
import argparse,hashlib,json,os,sys
from pathlib import Path
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--owner',required=True,type=lambda v:str(UUID(v)))
    p.add_argument('--auth-session',required=True,type=lambda v:str(UUID(v)))
    p.add_argument('--apply',action='store_true')
    return p.parse_args(argv)

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def plan(store):
    tasks=[];blocked=[]
    for row in sorted(store.list_tasks(),key=lambda t:t['task_id']):
        task={k:row.get(k) for k in ('task_id','title','state','generation','stop_requested_at')}
        work=store.task_worker_state(row['task_id'],limit=2000)
        reasons=[]
        if row['state'] in ('running','waiting_gate') or (row.get('stop_requested_at') is not None and row['state']!='stopped'):
            reasons.append('Stop and confirm quiescence before reset')
        if any(j['state'] in ('pending','paused','running') for j in work['worker_jobs']):reasons.append('worker jobs unresolved')
        if any(a['state'] in ('reserved','dispatched','uncertain') for a in work['provider_attempts']):reasons.append('provider accounting unresolved')
        elif work['provider_attempts'] and row['state']!='stopped':reasons.append('provider history requires Stop/tombstone path, never hard delete')
        # A capped inventory cannot prove absence; refuse rather than undercount.
        if len(work['worker_jobs'])>=2000 or len(work['provider_attempts'])>=2000:reasons.append('work inventory limit reached')
        task['work_fingerprint']=fingerprint(work);tasks.append(task)
        if reasons:blocked.append({'task_id':row['task_id'],'reasons':reasons})
    body={'task_count':len(tasks),'tasks':tasks,'blocked':blocked}
    return body|{'fingerprint':fingerprint(body)}

def apply(store,reviewed,*,confirm=input):
    if reviewed['blocked']:raise ValueError('unresolved work; Stop/reconcile first, no task changed')
    expected='DELETE '+str(reviewed['task_count'])+' TASKS'
    if confirm('Type "'+expected+'" to apply this owner-scoped reset: ')!=expected:raise ValueError('typed confirmation did not match; no task changed')
    if plan(store)!=reviewed:raise ValueError('reviewed task set changed; rerun dry review')
    completed=[]
    for task in reviewed['tasks']:
        # Store policy remains final authority: pending Stop/provider history
        # cannot be bypassed by this script. Each deletion is audit-recorded.
        try:store.delete_task(task['task_id'],actor='owner existing-task reset')
        except Exception as exc:
            raise RuntimeError('Reset stopped at task '+task['task_id']+' after '+str(len(completed))+' task(s). Rerun dry review; no automatic retry. '+type(exc).__name__) from exc
        completed.append(task['task_id'])

def main(argv=None):
    args=parse_args(argv)
    if not (ROOT/'pyproject.toml').is_file() or not (ROOT/'src/delivery_api/postgres_store.py').is_file():raise SystemExit('Not a delivery-api checkout')
    from delivery_api import config
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope,tenant_context
    dsn=config.DATABASE_URL
    if not dsn or not dsn.startswith(('postgres://','postgresql://','host=')):raise SystemExit('Explicit PostgreSQL DELIVERY_DATABASE_URL required; no SQLite/default target')
    store=PostgresStore(dsn)
    with tenant_context(TenantScope(args.owner,args.auth_session)):
        if not store.auth_session_active():raise SystemExit('Owner/auth session is not active; no task read or changed')
        print('Repository:',ROOT)
        print('Owner:',args.owner)
        print('Mode:','APPLY after typed confirmation' if args.apply else 'DRY RUN, no changes')
        reviewed=plan(store);print(json.dumps(reviewed,indent=2,sort_keys=True))
        if args.apply:
            apply(store,reviewed)
            print('Reset applied. Accounting, audit and immutable evidence were retained.')
        else:print('Nothing changed. Review blockers and rerun with --apply only when ready.')

if __name__=='__main__':
    import importlib.util
    from pathlib import Path
    _settings_path=Path(__file__).resolve().parent/'ops/settings.py'
    _spec=importlib.util.spec_from_file_location('ops_settings_entry',_settings_path)
    _settings=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_settings)
    try:_settings.apply_environment(Path(__file__).resolve().parents[1])
    except _settings.SettingsError as exc:raise SystemExit('SETTINGS ABORTED: '+str(exc))

if __name__=='__main__':main()
