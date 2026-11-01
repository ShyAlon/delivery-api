"""Explicit owner administrative override, never proof of process termination."""
import hashlib,json,time
from .tenancy import current_scope,TenantAccessDenied

WARNING='Force closes job records and fences future work. Worker exit is UNVERIFIED. Accepted calls may still bill. Provider/compute accounting is unchanged. No task is deleted or unhidden.'


def execute(store,table,operation,sql,args=(),*,write=False):
    try:return store._execute_native(sql,args,write=write)
    except Exception as exc:
        if getattr(exc,'sqlstate',None)=='42501':
            raise ValueError('FORCE STOP denied: '+table+' '+operation+' (SQLSTATE 42501; ACL/RLS or operator_state_events trigger). Batch rolled back. Inspect privileges and immutable session binding; do not grant blindly.') from None
        raise


def preflight(store):
    checks={}
    for table,privileges in (('tasks','SELECT,UPDATE'),('worker_jobs','SELECT,UPDATE'),('audit_log','SELECT,INSERT'),('engine_sessions','SELECT'),('provider_attempts','SELECT'),('retail_compute_segments','SELECT')):
        row=store._execute_native("SELECT bool_and(has_table_privilege(current_user,%s,p)) AS allowed FROM unnest(string_to_array(%s,',')) AS p",('public.'+table,privileges)).fetchone()
        checks[table]=row['allowed']
    missing=[t for t,ok in checks.items() if not ok]
    if missing:raise ValueError('FORCE STOP preflight missing table privileges: '+', '.join(missing)+'; no changes. Owner must inspect ACLs before repair.')
    return checks


def plan(store,*,lock=False):
    preflight(store)
    owner=current_scope().owner_id
    if not store.auth_session_active():raise TenantAccessDenied('active owner sign-in required')
    # Include tombstones. Never widen a signed-in owner to a global admin scan.
    execute(store,'tasks','SELECT FOR UPDATE' if lock else 'SELECT','SELECT task_id FROM tasks WHERE owner_id=%s ORDER BY task_id'+(' FOR UPDATE' if lock else ''),(owner,)).fetchall()
    rows=execute(store,'worker_jobs','SELECT FOR UPDATE' if lock else 'SELECT',"SELECT j.job_id,j.task_id,j.session_id,j.generation,j.state,j.updated_at,t.title,t.state AS task_state,t.generation AS task_generation,t.deleted_at,t.stop_requested_at FROM worker_jobs j JOIN tasks t ON t.owner_id=j.owner_id AND t.task_id=j.task_id WHERE j.owner_id=%s AND j.state IN ('pending','running','paused') ORDER BY j.job_id LIMIT 10001"+(' FOR UPDATE OF j' if lock else ''),(owner,)).fetchall()
    if len(rows)>10000:raise ValueError('more than 10000 jobs; no partial batch permitted')
    records=[dict(r) for r in rows]
    accounting=store._execute_native("SELECT 'provider' AS ledger,p.attempt_id AS id,p.job_id,p.state FROM provider_attempts p WHERE p.owner_id=%s AND p.job_id=ANY(%s) UNION ALL SELECT 'compute',c.segment_key,c.job_id,c.state FROM retail_compute_segments c WHERE c.owner_id=%s AND c.job_id=ANY(%s) ORDER BY ledger,id",(owner,[r['job_id'] for r in records],owner,[r['job_id'] for r in records])).fetchall()
    binding=store._execute_native("SELECT j.job_id,j.generation AS job_generation,s.generation AS binding_generation FROM worker_jobs j LEFT JOIN engine_sessions s ON s.session_id=j.session_id AND s.owner_id=j.owner_id AND s.task_id=j.task_id WHERE j.owner_id=%s AND j.job_id=ANY(%s) AND (s.session_id IS NULL OR s.generation<>j.generation) ORDER BY j.job_id",(owner,[r['job_id'] for r in records])).fetchall()
    repair_ids=[r['job_id'] for r in binding]
    for item in binding:
        job=next(r for r in records if r['job_id']==item['job_id'])
        if (item['binding_generation'] is None or item['binding_generation']<=item['job_generation']
            or job['task_generation']<=job['generation'] or job['stop_requested_at'] is None
            or any(r['job_id']==item['job_id'] for r in accounting)):
            raise ValueError('fenced repair unavailable for '+item['job_id']+': newer task/session fence, durable Stop and no accounting required')
    if repair_ids:
        present=store._execute_native("SELECT to_regprocedure('public.requi_force_close_fenced_job(text,text,text)') IS NOT NULL AS present").fetchone()['present']
        if not present:raise ValueError('apply migration 20261101073000_fenced_job_force_close.sql first; no force changes')
    body={'owner_id':owner,'jobs':records,'accounting':[dict(r) for r in accounting],'fenced_repair_ids':repair_ids}
    phrase='FORCE STOP '+str(len(records))+' JOBS'+(' REPAIR '+','.join(repair_ids) if repair_ids else '')
    return body|{'confirmation':phrase,'fingerprint':hashlib.sha256(json.dumps(body,sort_keys=True,default=str).encode()).hexdigest(),'warning':WARNING}


def apply(store,fingerprint,confirmation):
    with store._task_operation_guard('task.stop'):
        reviewed=plan(store,lock=True)
        if fingerprint!=reviewed['fingerprint']:raise ValueError('plan changed; review again; nothing forced')
        expected=reviewed['confirmation']
        if confirmation!=expected:raise ValueError('typed confirmation mismatch; nothing forced')
        scope=current_scope();now=time.time();tasks=set();results=[]
        for job in reviewed['jobs']:
            tid=job['task_id']
            if job['job_id'] in reviewed['fenced_repair_ids']:
                execute(store,'worker_jobs/audit_log','SELECT terminal-only fenced repair',
                    'SELECT public.requi_force_close_fenced_job(%s,%s,%s)',
                    (job['job_id'],'FORCE CLOSE '+job['job_id'],fingerprint),write=True)
                results.append({'job_id':job['job_id'],'task_id':tid,'state':'failed','worker_exit_verified':False,'fenced_repair':True})
                continue
            if tid not in tasks:
                # Always bump generation, even if an earlier Stop already exists.
                execute(store,'tasks','UPDATE stop fence',"UPDATE tasks SET generation=generation+1,state='stop_requested',stop_requested_at=COALESCE(stop_requested_at,%s),updated_at=%s WHERE task_id=%s AND owner_id=%s",(now,now,tid,scope.owner_id),write=True)
                tasks.add(tid)
            # Failed, NOT done/stopped: administrative closure is not completion.
            execute(store,'worker_jobs','UPDATE administrative failed',"UPDATE worker_jobs SET state='failed',updated_at=%s WHERE job_id=%s AND owner_id=%s AND state IN ('pending','running','paused')",(now,job['job_id'],scope.owner_id),write=True)
            evidence={'job_id':job['job_id'],'session_id':job['session_id'],'prior_state':job['state'],'prior_generation':job['generation'],
                'task_generation_before':job['task_generation'],'hidden':job['deleted_at'] is not None,
                'owner_id':scope.owner_id,'auth_session_id':scope.session_id,'at':now,'forced_override':True,
                'worker_exit_verified':False,'accounting_changed':False,'plan_fingerprint':fingerprint}
            try:audit_id=store.audit(scope.owner_id,'worker.force_stop',tid,evidence)
            except Exception as exc:
                if getattr(exc,'sqlstate',None)=='42501':raise ValueError('FORCE STOP denied: audit_log INSERT RETURNING id (SQLSTATE 42501; ACL/RLS/sequence). Batch rolled back; no blind retry.') from None
                raise
            results.append({'job_id':job['job_id'],'task_id':tid,'state':'failed','audit_id':audit_id,'worker_exit_verified':False})
        return {'results':results,'warning':WARNING,'accounting':reviewed['accounting'],'forced_count':len(results)}
