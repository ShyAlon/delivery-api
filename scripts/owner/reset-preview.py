#!/usr/bin/env python3
"""Owner-scoped test-preview reset. Dry by default, mandatory verified statistics snapshot."""
import argparse,importlib.util,json,sys
from pathlib import Path
from uuid import UUID

# Identities, prepaid balance, payment history, settings and global policies survive.
SURVIVE={'users','credit_accounts','credit_ledger','credit_adjustments','payment_purchases','payment_credits','payment_events','admission_limits','own_test_expense_profiles','user_task_policy','retail_scale_migrations','operator_principals','audit_log','user_task_policy_changes','operator_principals','engine_quality_config'}
APPROVED_PROJECT='mpdsycxsppkgndhjysln'
APPROVED_OWNER='9ed32462-3dd0-4222-90b3-92cb38e00911'
ACCOUNTING={'provider_attempts','credit_charges','retail_compute_segments','retail_contributions','staged_provider_responses','expense_alert_outbox','admission_totals','admission_total_entries','expense_total_entries','retail_totals'}


def inventory(conn,owner):
    columns={}
    for table,column in conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"):
        columns.setdefault(table,set()).add(column)
    tasks=[r[0] for r in conn.execute('SELECT task_id FROM public.tasks WHERE owner_id=%s',(owner,))]
    sessions=[r[0] for r in conn.execute('SELECT session_id FROM public.engine_sessions WHERE owner_id=%s',(owner,))]
    threads=[r[0] for r in conn.execute('SELECT published_thread_id FROM public.engine_sessions WHERE owner_id=%s AND published_thread_id IS NOT NULL',(owner,))]
    sessions=list(set(sessions+threads))
    jobs=[r[0] for r in conn.execute('SELECT job_id FROM public.worker_jobs WHERE owner_id=%s',(owner,))]
    attempts=[r[0] for r in conn.execute('SELECT attempt_id FROM public.provider_attempts WHERE owner_id=%s',(owner,))]
    from psycopg import sql
    rows=[]
    for table,cols in columns.items():
        field,values=next(((f,v) for f,v in (('owner_id',[owner]),('thread_id',sessions),('task_id',tasks),('session_id',sessions),('execution_session_id',sessions),('job_id',jobs),('attempt_id',attempts),('user_id',[owner])) if f in cols),(None,[]))
        if not field:continue
        query=sql.SQL('SELECT count(*) FROM public.{} WHERE {}::text=ANY(%s)').format(sql.Identifier(table),sql.Identifier(field))
        count=conn.execute(query,(values,)).fetchone()[0]
        if count:rows.append({'table':table,'rows':count,'action':'SURVIVE' if table in SURVIVE else 'ACCOUNTING CHOICE REQUIRED' if table in ACCOUNTING else 'DELETE CANDIDATE','scope_column':field})
    return rows


def deletion_order(conn,rows,accounting):
    selected={r['table'] for r in rows if r['action']=='DELETE CANDIDATE' or (accounting=='wipe' and r['action']=='ACCOUNTING CHOICE REQUIRED')}
    edges=[]
    for child,parent in conn.execute("SELECT c.conrelid::regclass::text,c.confrelid::regclass::text FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE c.contype='f' AND n.nspname='public'"):
        child=child.removeprefix('public.');parent=parent.removeprefix('public.')
        if child in selected and parent in selected:edges.append((child,parent))
    order=[]
    while selected:
        ready=sorted(t for t in selected if not any(parent==t and child in selected for child,parent in edges))
        if not ready:raise ValueError('cyclic FK dependency requires review; no deletion plan')
        order.extend(ready);selected.difference_update(ready)
    return order


def target_evidence(conn,dsn,owner):
    from psycopg.conninfo import conninfo_to_dict
    from psycopg import sql
    import re
    host=conninfo_to_dict(dsn).get('host','')
    kind='local' if host in ('localhost','127.0.0.1','::1') or host.startswith('/') else 'supabase' if host.endswith('.supabase.co') or host.endswith('.pooler.supabase.com') else 'remote-unclassified'
    match=re.fullmatch(r'db\.([a-z0-9]+)\.supabase\.co',host)
    project=match.group(1) if match else None
    login=conninfo_to_dict(dsn).get('user','')
    if kind=='supabase' and not project and re.fullmatch(r'postgres\.[a-z0-9]+',login):project=login.split('.',1)[1]
    stats=[]
    for table,key,age in (('auth.users','id','created_at'),('public.tasks','owner_id','created_at'),('public.engine_sessions','owner_id','created_at'),('public.worker_jobs','owner_id','created_at'),('public.credit_accounts','owner_id','updated_at'),('public.credit_ledger','owner_id','recorded_at'),('public.payment_purchases','owner_id','created_at')):
        schema,name=table.split('.')
        query=sql.SQL('SELECT {}::text,count(*),min({}),max({}) FROM {}.{} GROUP BY {} ORDER BY {}').format(sql.Identifier(key),sql.Identifier(age),sql.Identifier(age),sql.Identifier(schema),sql.Identifier(name),sql.Identifier(key),sql.Identifier(key))
        rows=conn.execute(query).fetchall()
        stats.append({'table':table,'row_count':sum(r[1] for r in rows),'distinct_owners':len(rows),
            'owners':[{'owner_id':r[0],'is_selected_owner':r[0]==owner,'rows':r[1],'oldest':r[2],'newest':r[3]} for r in rows]})
    return {'host_kind':kind,'supabase_project_ref':project,'host_name_excluded':True,
        'only_selected_auth_user':all(x['is_selected_owner'] for x in stats[0]['owners']) and bool(stats[0]['owners']),
        'owner_inventory':stats,'note':'Only-owner status and old rows do not prove a database is disposable or not production.'}


def snapshot(conn,owner,directory):
    import datetime,hashlib,os,zipfile
    queries={
      'provider_by_session_stage':"SELECT j.session_id,j.kind,p.state,count(*) AS attempts,sum(p.expense_actual) AS expense_actual_units,sum(p.expense_reserved) AS expense_reserved_units,sum(p.retail_actual) AS retail_actual_units,min(p.created_at) AS first_created_at,max(p.settled_at) AS last_settled_at FROM public.provider_attempts p JOIN public.worker_jobs j ON j.job_id=p.job_id WHERE p.owner_id=%s GROUP BY j.session_id,j.kind,p.state ORDER BY 1,2,3",
      'retail_by_session_stage':"SELECT j.session_id,r.phase_key,r.kind,count(*) AS contributions,sum(r.input_tokens) AS input_tokens,sum(r.output_tokens) AS output_tokens,sum(r.deterministic_ms) AS deterministic_ms,sum(r.exact_units) AS exact_credit_units,sum(r.posted_units) AS posted_credit_units FROM public.retail_contributions r JOIN public.worker_jobs j ON j.job_id=r.job_id WHERE r.owner_id=%s GROUP BY j.session_id,r.phase_key,r.kind ORDER BY 1,2,3",
      'compute_by_session_stage':"SELECT j.session_id,c.phase_key,c.state,count(*) AS segments,sum(c.deterministic_ms) AS deterministic_ms,min(c.started_at) AS first_started_at,max(c.settled_at) AS last_settled_at FROM public.retail_compute_segments c JOIN public.worker_jobs j ON j.job_id=c.job_id WHERE c.owner_id=%s GROUP BY j.session_id,c.phase_key,c.state ORDER BY 1,2,3",
      'job_durations':"SELECT session_id,kind,state,count(*) AS jobs,sum(updated_at-created_at) AS recorded_wall_seconds FROM public.worker_jobs WHERE owner_id=%s GROUP BY session_id,kind,state ORDER BY 1,2,3",
      'provider_usage_evidence':"SELECT j.session_id,j.kind,p.attempt_id,p.state,p.usage,p.raw_usage,p.expense_actual,p.settled_at FROM public.provider_attempts p JOIN public.worker_jobs j ON j.job_id=p.job_id WHERE p.owner_id=%s ORDER BY p.attempt_id",
    }
    data={'owner_id':owner,'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'units':{'expense_per_usd':10**12,'credit_units_per_credit':10**9},
          'caveats':['Job wall seconds are recorded state timestamp differences, not active CPU/inference duration.','Unknown/unsettled amounts stay null; no invented zero.','Provider usage evidence preserves reported token categories where no normalized retail contribution exists.'],
          'inventory':inventory(conn,owner)}
    for name,query in queries.items():
        cursor=conn.execute(query,(owner,));keys=[d.name for d in cursor.description]
        data[name]=[dict(zip(keys,row)) for row in cursor]
    payload=json.dumps(data,sort_keys=True,indent=2,default=str).encode()
    directory=Path(directory).expanduser();directory.mkdir(parents=True,exist_ok=True)
    path=directory/('statistics-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.zip')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as file:
        with zipfile.ZipFile(file,'w',zipfile.ZIP_DEFLATED) as bundle:bundle.writestr('statistics.json',payload)
        file.flush();os.fsync(file.fileno())
    with zipfile.ZipFile(path) as bundle:
        if bundle.testzip() is not None or bundle.read('statistics.json')!=payload:raise ValueError('statistics snapshot verification failed')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}


def reset(dsn,owner,accounting,*,confirm=input,directory=None):
    import psycopg
    from psycopg import sql
    if owner!=APPROVED_OWNER or accounting!='wipe':raise ValueError('only reviewed owner and accounting wipe supported')
    with psycopg.connect(dsn,connect_timeout=10) as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='60s'")
        evidence=target_evidence(conn,dsn,owner)
        if evidence['supabase_project_ref']!=APPROVED_PROJECT:raise ValueError('target differs from reviewed preview project; no changes')
        # Stop concurrent writers before snapshot and inventory. No CASCADE or disabled triggers.
        tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
        conn.execute(sql.SQL('LOCK TABLE {} IN EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier('public',t) for t in tables)))
        if conn.execute("SELECT EXISTS(SELECT 1 FROM public.engine_sessions a JOIN public.engine_sessions b ON b.owner_id<>a.owner_id AND b.published_thread_id=a.published_thread_id WHERE a.owner_id=%s AND a.published_thread_id IS NOT NULL)",(owner,)).fetchone()[0]:raise ValueError('shared published checkpoint thread; reset refused')
        rows=inventory(conn,owner);order=deletion_order(conn,rows,'wipe')
        phrase='RESET PREVIEW '+APPROVED_PROJECT+' OWNER '+owner+' WIPE ACCOUNTING'
        print(json.dumps({'deletes':rows,'order':order,'warning':'All owner preview work and test accounting will be deleted. Auth/prepaid/payment/policy/audit and other owner data survive. Stop all preview producers first; this is not a process kill.'},indent=2,default=str))
        if confirm('Type "'+phrase+'": ')!=phrase:raise ValueError('confirmation mismatch; no changes')
        exported=snapshot(conn,owner,directory or Path.home()/'Downloads/requi-preview-reset')
        print('VERIFIED STATISTICS SNAPSHOT '+json.dumps(exported))
        scope={
          'owner_id':[owner],'user_id':[owner],
          'task_id':[r[0] for r in conn.execute('SELECT task_id FROM public.tasks WHERE owner_id=%s',(owner,))],
          'session_id':[r[0] for r in conn.execute('SELECT session_id FROM public.engine_sessions WHERE owner_id=%s',(owner,))],
          'job_id':[r[0] for r in conn.execute('SELECT job_id FROM public.worker_jobs WHERE owner_id=%s',(owner,))],
          'attempt_id':[r[0] for r in conn.execute('SELECT attempt_id FROM public.provider_attempts WHERE owner_id=%s',(owner,))]}
        scope['execution_session_id']=scope['session_id']
        scope['thread_id']=list(set(scope['session_id']+[r[0] for r in conn.execute('SELECT published_thread_id FROM public.engine_sessions WHERE owner_id=%s AND published_thread_id IS NOT NULL',(owner,))]))
        by_table={r['table']:r for r in rows}
        for table in order:
            field=by_table[table]['scope_column']
            count=conn.execute(sql.SQL('DELETE FROM public.{} WHERE {}::text=ANY(%s)').format(sql.Identifier(table),sql.Identifier(field)),(scope[field],)).rowcount
            print('DELETED '+table+' '+str(count))
        # Existing delete triggers maintain derived totals; no other-owner reset.
        conn.execute("INSERT INTO public.audit_log(actor,action,owner_id,detail,ts) VALUES(%s,'preview.owner_reset',%s,%s,extract(epoch FROM statement_timestamp()))",(owner,owner,json.dumps({'project':APPROVED_PROJECT,'accounting':'wipe','statistics':exported,'tables':order})))
        remaining=conn.execute('SELECT (SELECT count(*) FROM tasks WHERE owner_id=%s)+(SELECT count(*) FROM worker_jobs WHERE owner_id=%s)+(SELECT count(*) FROM engine_sessions WHERE owner_id=%s)',(owner,owner,owner)).fetchone()[0]
        if remaining:raise ValueError('reset incomplete; transaction rolled back; snapshot retained')
    print('RESET COMMITTED. Auth/prepaid/payment/policy/audit and other users preserved. Snapshot retained. No provider refund implied.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('repo',type=Path);p.add_argument('--env-file',type=Path);p.add_argument('--owner');p.add_argument('--accounting',choices=('keep','wipe'),default='wipe');p.add_argument('--apply',action='store_true');p.add_argument('--snapshot',action='store_true',help='write verified statistics zip, no deletion');args=p.parse_args()
    root=args.repo.expanduser().resolve()
    spec=importlib.util.spec_from_file_location('settings',root/'scripts/ops/settings.py');settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
    values=settings.load(root,explicit=args.env_file)
    global diagnostic_dsn
    diagnostic_dsn=values['REQUI_ADMIN_DATABASE_URL']
    owner=str(UUID(args.owner or values.get('RESET_OWNER_ID') or input('Preview owner UUID: ').strip()))
    import psycopg
    with psycopg.connect(values['REQUI_ADMIN_DATABASE_URL'],connect_timeout=10,options='-c default_transaction_read_only=on') as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='10s'")
        for table in ('tasks','worker_jobs','provider_attempts'):
            if conn.execute('SELECT row_security_active(%s::regclass)',('public.'+table,)).fetchone()[0]:raise SystemExit('Full inventory unavailable under RLS; no changes.')
        target=conn.execute('SELECT current_database(),inet_server_port()').fetchone()
        exported=snapshot(conn,owner,Path.home()/'Downloads/requi-preview-reset') if args.snapshot else None
        print(json.dumps({'statistics_snapshot':exported,'owner_id':owner,'target':target,'target_evidence':target_evidence(conn,values['REQUI_ADMIN_DATABASE_URL'],owner),'target_reviewed_test_only':owner==APPROVED_OWNER,'accounting_requested':args.accounting,'inventory':inventory(conn,owner),'candidate_deletion_order':deletion_order(conn,inventory(conn,owner),args.accounting),'foreign_keys':[{'child':r[0],'parent':r[1],'definition':r[2]} for r in conn.execute("SELECT c.conrelid::regclass::text,c.confrelid::regclass::text,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE c.contype='f' AND n.nspname='public' ORDER BY 1,2")],
          'survives':['auth user and sign-in sessions','prepaid balance/credit ledger/adjustments','payment history','account/global policies and operator registry','audit history','source files outside PostgreSQL'],
          'unverified':['Stop producers before apply; table locks do not kill worker processes','source files outside PostgreSQL are not deleted'],
          'apply_scope':'selected owner only; never auto-expand to all owners','accounting_wipe_intent':True,'changes':False},indent=2,default=str))
    if args.apply and not sys.stdin.isatty():raise ValueError('interactive terminal required; no piped reset confirmation')
    if args.apply:reset(values['REQUI_ADMIN_DATABASE_URL'],owner,args.accounting)
    if args.apply:return
    print('DRY INVENTORY ONLY. No changes. Accounting is not zeroed/refunded; keep can prevent parent job/session deletion.')


if __name__=='__main__':
    import psycopg
    try:main()
    except (psycopg.errors.LockNotAvailable,psycopg.errors.QueryCanceled):
        print('RESET LOCK/TIMEOUT: transaction rolled back; no reset committed. Current sessions follow, not proof of the expired wait edge.')
        from delivery_api.db_lock_diagnostics import report
        report(diagnostic_dsn)
        raise SystemExit('Stop preview producers and review the holder before retrying. No session was cancelled/terminated.') from None
    except (KeyError,ValueError) as exc:raise SystemExit('RESET REFUSED: '+str(exc)) from None
    except Exception as exc:raise SystemExit('RESET FAILED ('+type(exc).__name__+'); transaction rolled back unless commit was already reported. Inspect dry inventory; snapshot retained if written. Private driver details excluded.') from None
