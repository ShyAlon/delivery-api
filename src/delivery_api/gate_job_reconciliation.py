"""Retire superseded phase jobs only at a settled waiting gate.

The same planner serves the in-transaction gate guard and the standalone owner
repair command. No artifact, accounting, session lease or approval is rewritten.
"""
import hashlib
import json
import time


def reconcile(execute,task_id,phase,*,apply=False,expected=None,only_job=None,actor=None):
    """Caller owns a transaction. Lock in task -> session -> job order."""
    task=execute('SELECT * FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
    if not task:raise ValueError('Owned task not found')
    if task['state']!='waiting_gate' or task['active_phase']!=phase or task.get('deleted_at') is not None or task.get('stop_requested_at') is not None:
        raise ValueError('Repair requires the current non-stopped waiting gate')
    gate=execute("SELECT state FROM gates WHERE task_id=%s AND after_phase=%s",(task_id,phase)).fetchone()
    row=execute('SELECT state FROM phases WHERE task_id=%s AND phase_key=%s',(task_id,phase)).fetchone()
    if not gate or gate['state']!='waiting' or not row or row['state']!='awaiting_approval':raise ValueError('Exact waiting phase and gate required')
    session=execute('SELECT * FROM engine_sessions WHERE session_id=%s FOR UPDATE',(task['engine_session_id'],)).fetchone()
    if not session or session['generation']!=task['generation'] or str(session['owner_id'])!=str(task['owner_id']) or session['task_id']!=task_id:
        raise ValueError('Current owner/session/generation binding required')
    now=time.time()
    if (session.get('lease_expires_at') or 0)>now:raise ValueError('Producer lease is still live; no reconciliation performed')
    jobs=[dict(j) for j in execute('SELECT * FROM worker_jobs WHERE task_id=%s AND generation=%s ORDER BY created_at,job_id FOR UPDATE',(task_id,task['generation'])).fetchall()]
    same=[j for j in jobs if j['kind']=='phase:'+phase and j['session_id']==session['session_id'] and str(j['owner_id'])==str(task['owner_id'])]
    if not same or same[-1]['state']!='done':raise ValueError('Latest exact phase job must be DONE')
    newer=same[-1]
    candidates=[j for j in same[:-1] if j['state']=='running' and j['created_at']<newer['created_at']]
    if only_job is not None:candidates=[j for j in candidates if j['job_id']==only_job]
    if not candidates:raise ValueError('No superseded RUNNING phase job matches this repair')
    # Another current job, including a newer queued attempt or a different phase,
    # is not stale merely because this phase once completed.
    chosen={j['job_id'] for j in candidates}
    if any(j['state'] in ('running','pending','paused') and j['job_id'] not in chosen for j in jobs):
        raise ValueError('Another current or unresolved worker exists; no reconciliation performed')
    for job in candidates:
        if job['updated_at']>newer['created_at']:raise ValueError('Old job changed after its successor began; investigate before repair')
        # Locks serialize late settlement. No refund, settlement or invented usage.
        attempts=execute('SELECT state FROM provider_attempts WHERE job_id=%s FOR UPDATE',(job['job_id'],)).fetchall()
        compute=execute('SELECT state FROM retail_compute_segments WHERE job_id=%s FOR UPDATE',(job['job_id'],)).fetchall()
        if any(a['state'] not in ('settled','cancelled') for a in attempts) or any(c['state']!='settled' for c in compute):
            raise ValueError('Superseded job has unsettled accounting; no reconciliation performed')
    plan={'version':1,'task_id':task_id,'owner_id':str(task['owner_id']),'session_id':session['session_id'],
          'generation':task['generation'],'phase':phase,'successor_job_id':newer['job_id'],
          'successor_created_at':newer['created_at'],'successor_updated_at':newer['updated_at'],
          'changes':[{'job_id':j['job_id'],'from':'running','to':'failed','attempt':j['attempt'],'updated_at':j['updated_at']} for j in candidates],
          'accounting_changed':False,'artifacts_changed':False,'gate_changed':False,'dispatch':False}
    fingerprint=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if apply:
        if expected is not None and expected!=fingerprint:raise ValueError('Repair plan changed since dry run; inspect a fresh dry run')
        for change in plan['changes']:
            changed=execute("UPDATE worker_jobs SET state='failed',updated_at=%s WHERE job_id=%s AND state='running' AND updated_at=%s",(now,change['job_id'],change['updated_at']))
            if changed.rowcount!=1:raise ValueError('Worker changed during repair')
        execute('INSERT INTO audit_log(actor,action,task_id,owner_id,detail,ts) VALUES (%s,%s,%s,%s,%s,%s)',
                (actor or str(task['owner_id']),'worker.superseded_at_gate',task_id,task['owner_id'],json.dumps({**plan,'fingerprint':fingerprint}),now))
    return {**plan,'fingerprint':fingerprint,'applied':apply}
