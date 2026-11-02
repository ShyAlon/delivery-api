"""Bounded operator audit projections and current-owner guarded actions."""
import json
from .server_log_buffer import scrub
ACTIONS=('provider.attempt.uncertain','provider.attempt.cancelled','provider.attempt.released','provider.attempt.release_failed','task.deleted','task.operator_stop_requested','run.stopped','compute.segment_closed_no_provider_call','compute.segment_released_by_owner','compute.segment_reconciled_no_charge','audit.resolution')

def project(row,owner):
    try:detail=json.loads(row['detail']) if isinstance(row['detail'],str) else row['detail'] or {}
    except (ValueError,TypeError):detail={}
    # Never forward arbitrary historic audit payloads, which may contain prompts.
    safe={k:scrub(str(detail[k]))[:500] for k in ('attempt_id','job_id','outcome','admission','automatic_retry','reason','refusal','error','accounting_retained','worker_exit_verified','resolution','audit_id') if k in detail}
    own=str(row['owner_id'])==owner;state=row.get('attempt_state');job_state=row.get('job_state')
    proof=detail.get('evidence');release=False
    if own and state=='dispatched' and job_state in ('done','failed','paused'):
        try:
            from .attempt_ledger import release_proof
            release_proof({'state':state},proof);release=True
        except ValueError:pass
    return {'id':row['id'],'ts':row['ts'],'event':row['action'],'actor':scrub(row['actor'])[:120],
        'owner_id':str(row['owner_id']),'task_id':row['task_id'],'detail':safe,'current_owner':own,
        'attempt_id':detail.get('attempt_id'),'job_id':row.get('job_id') or detail.get('job_id'),
        'attempt_state':state,'job_state':job_state,'task_hidden':row.get('deleted_at') is not None,
        'actions':{'hide':{'available':bool(own and row.get('visible_task_id') and row.get('deleted_at') is None),'reason':'Hide keeps all accounting and audit evidence.' if own else 'Other-owner rows are evidence only.'},
        'cancel':{'available':bool(own and state=='reserved' and job_state in ('done','failed','paused')),'reason':'Only an undispatched reservation on a non-running job may be cancelled.'},
        'release':{'available':release,'reason':'Stored no-request-byte proof is eligible; the ledger rechecks all evidence and release limits.' if release else 'Uncertain outcomes are not proof that no request was sent. No financial write-off or invented zero bill is allowed.'}}}

COLUMNS="""a.id,a.ts,a.actor,a.action,a.detail,a.task_id,a.owner_id,p.state AS attempt_state,
 p.job_id,w.state AS job_state,t.deleted_at,t.task_id AS visible_task_id"""
JOINS=""" FROM public.audit_log a
 LEFT JOIN public.provider_attempts p ON p.attempt_id=(a.detail::jsonb)->>'attempt_id' AND p.owner_id=a.owner_id
 LEFT JOIN public.worker_jobs w ON w.job_id=COALESCE(p.job_id,(a.detail::jsonb)->>'job_id') AND w.owner_id=a.owner_id
 LEFT JOIN public.tasks t ON t.task_id=a.task_id AND t.owner_id=a.owner_id """

def list_rows(reader,store,*,owner,before=None,limit=50,event=None):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('limit must be 1 to 100')
    if before is not None and (type(before) is not int or before<1):raise ValueError('positive audit cursor required')
    if event and event not in ACTIONS:raise ValueError('unsupported audit event filter')
    with reader._read(store,'logs.read') as (conn,authority):
        where=' WHERE a.action=ANY(%s)';params=[list(ACTIONS)]
        if before:where+=' AND a.id<%s';params.append(before)
        if event:where+=' AND a.action=%s';params.append(event)
        params.append(limit+1)
        rows=conn.execute('SELECT '+COLUMNS+JOINS+where+' ORDER BY a.id DESC LIMIT %s',params).fetchall()
        page=[project(dict(row),owner) for row in rows[:limit]]
        return {'scope':'reviewed_cross_user_operator_read','events':page,'truncated':len(rows)>limit,
            'next_before':page[-1]['id'] if len(rows)>limit else None,'event_types':list(ACTIONS),
            'resolution_scope':'current_owner_only','operator_subject':authority['operator_subject'],'registry_version':authority['registry_version']}


def resolve(store,audit_id,action,expected_attempt_state):
    from .tenancy import current_scope
    from .attempt_ledger import AdmissionDenied
    owner=current_scope().owner_id
    # Called inside the store's normal scoped transaction, never cross-owner role.
    store._execute_native('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
    row=store._execute_native('SELECT '+COLUMNS+JOINS+' WHERE a.id=%s AND a.owner_id=%s',(audit_id,owner)).fetchone()
    if not row:raise KeyError('Owned audit record not found')
    item=project(dict(row),owner)
    if action not in ('hide','cancel','release'):raise AdmissionDenied('No eligible proof-checked release or write-off is available')
    if action in ('cancel','release') and item['attempt_state']!=expected_attempt_state:raise AdmissionDenied('Attempt changed; refresh and review again')
    if not item['actions'][action]['available']:raise AdmissionDenied(item['actions'][action]['reason'])
    if action=='hide':store.stop_hide_task(item['task_id'],confirm=True,actor=owner,hide=True)
    else:
        # Lock and recheck job; no queued/running worker may still dispatch it.
        job=store._execute_native('SELECT state FROM worker_jobs WHERE job_id=%s FOR UPDATE',(item['job_id'],)).fetchone()
        if not job or job['state'] not in ('done','failed','paused'):raise AdmissionDenied('Job changed; reservation not cancelled')
        detail=json.loads(row['detail']) if isinstance(row['detail'],str) else row['detail']
        result=store.transition_provider_attempt(item['attempt_id'],action,detail['evidence'] if action=='release' else {'refusal':'explicit owner audit resolution'})
        if result['state']!=('released' if action=='release' else 'cancelled'):raise AdmissionDenied('Resolution unverified')
    store.audit(owner,'audit.resolution',item['task_id'],{'audit_id':audit_id,'resolution':action,'attempt_id':item['attempt_id'],'accounting_retained':True})
    return {'resolved':True,'resolution':action,'accounting_retained':True,'automatic_retry':False}
