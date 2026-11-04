"""Compact numeric evidence only. No prompt, answer, provider bytes or credentials."""
from decimal import Decimal
from .tenancy import TenantAccessDenied


def breakdown(store,task_id):
    if not store._execute_native('SELECT public.requi_test_task_owner() AS allowed').fetchone()['allowed']:
        raise TenantAccessDenied('reviewed test owner required')
    task=store.task(task_id)
    if not task or not task['is_test']:raise KeyError('owned test task required')
    rows=store._execute_native('''SELECT r.kind,count(*) AS contributions,
        sum(r.exact_units) AS exact_units,sum(r.posted_units) AS posted_units,
        sum(r.deterministic_ms) AS compute_ms
        FROM retail_contributions r JOIN worker_jobs j USING(job_id)
        WHERE j.task_id=%s GROUP BY r.kind ORDER BY r.kind''',(task_id,)).fetchall()
    parts={r['kind']:{'contributions':r['contributions'],'exact_units':str(r['exact_units']),
        'posted_units':str(r['posted_units']),'compute_ms':str(r['compute_ms'])} for r in rows}
    jobs=store._execute_native('''SELECT j.job_id,j.state,d.payload
        FROM worker_jobs j LEFT JOIN decision_reviews d
        ON d.task_id=j.task_id AND d.wait_id='child-job:'||j.job_id AND d.revision='child-completion-v1'
        WHERE j.task_id=%s ORDER BY j.created_at,j.job_id''',(task_id,)).fetchall()
    # Only allowlisted counters survive. Do not pass through arbitrary payloads.
    child=[]
    import json
    for row in jobs:
        payload=row['payload'] or {}
        if isinstance(payload,str):payload=json.loads(payload)
        item={'job_id':row['job_id'],'state':row['state'],'reaped':payload.get('reaped') is True}
        if 'compute_ms' in payload:item['compute_ms']=str(Decimal(str(payload['compute_ms'])))
        timings=payload.get('timings',{})
        item['timings_ms']={k:v for k,v in timings.items() if k in ('slot_wait','run','setup','supervisor','completion') and type(v) is int and v>=0}
        item['provider_timings']=[{k:v for k,v in timing.items() if k in ('stage','transport_ms','journal_ms','settlement_ms') and (isinstance(v,str) if k=='stage' else type(v) is int and v>=0)} for timing in payload.get('provider_timings',[])][:100]
        child.append(item)
    provider=store._execute_native('''SELECT p.attempt_id,p.state,s.stage AS stage,
        p.created_at,p.provider_started_at,p.settled_at,s.dispatched_at,s.completed_at
        FROM provider_attempts p JOIN worker_jobs j USING(job_id)
        LEFT JOIN staged_provider_responses s ON s.attempt_id=p.attempt_id AND s.owner_id=p.owner_id
        WHERE j.task_id=%s ORDER BY p.created_at,p.attempt_id''',(task_id,)).fetchall()
    attempts=[]
    for r in provider:
        item={'attempt_id':r['attempt_id'],'state':r['state'],'stage':r['stage']}
        for label,start,end in (('admission_to_dispatch','created_at','provider_started_at'),('dispatch_to_settlement','provider_started_at','settled_at'),('broker_journal_window','dispatched_at','completed_at')):
            if r[start] is not None and r[end] is not None:item[label+'_ms']=max(0,round((r[end]-r[start])*1000))
        attempts.append(item)
    return {'task_id':task_id,'units_per_credit':1000000000,'parts':parts,
        'total_posted_units':str(sum(Decimal(p['posted_units']) for p in parts.values())),
        'total_exact_credits':str(sum(Decimal(p['exact_units']) for p in parts.values())/Decimal(1000000000)),
        'children':child,'provider_attempts':attempts,
        'timing_basis':'Parent monotonic stages and durable DB timestamps. Journal window includes admission and persistence, not pure HTTP latency.'}
