"""Read-only evidence from this invocation's owned fixture database.

Runs in the fixture container after signed HTTP Start/answer. Never accepts a DSN
or arbitrary SQL. No service modules, filesystem artifacts or forged child rows.
"""
import json
from pathlib import Path
import re
import sys


def inspect_task(task_id,*,stop_state=False):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task_id):raise ValueError('bounded fixture task identity required')
    state=json.loads(Path('/tmp/fixture-state.json').read_bytes())
    from psycopg.conninfo import conninfo_to_dict
    import psycopg
    fields=conninfo_to_dict(state['dsn'])
    if not fields['dbname'].startswith('test_tenant_') or fields.get('host')!='/fixture/socket' or fields.get('user')!='delivery_app':
        raise ValueError('owned fixture connection identity differs')
    fields['user']='sandbox'
    from fixture_services import OWNER,SESSION
    with psycopg.connect(**fields) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        task=conn.execute('SELECT owner_id,engine_session_id,state FROM tasks WHERE task_id=%s',(task_id,)).fetchone()
        if not task or str(task[0])!=OWNER:raise ValueError('owned task required')
        if stop_state:
            jobs=conn.execute('SELECT state FROM worker_jobs WHERE task_id=%s',(task_id,)).fetchall()
            attempts=conn.execute('SELECT p.state FROM provider_attempts p JOIN worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=%s',(task_id,)).fetchall()
            return {'task_state':task[2],'child_jobs':[r[0] for r in jobs],'provider_attempts':[r[0] for r in attempts]}
        if task[2]!='stopped':raise ValueError('owned stopped task required')
        sid=task[1]
        jobs=conn.execute('SELECT job_id,state,auth_session_id FROM worker_jobs WHERE task_id=%s ORDER BY created_at,job_id',(task_id,)).fetchall()
        if len(jobs)!=2 or any(row[1]!='done' or str(row[2])!=SESSION for row in jobs):
            raise ValueError('two completed owned Start/answer child jobs required')
        for job,_,_ in jobs:
            review=conn.execute("SELECT payload::jsonb FROM decision_reviews WHERE task_id=%s AND wait_id=%s AND revision='child-completion-v1'",(task_id,'child-job:'+job)).fetchone()
            if not review or review[0].get('reaped') is not True:raise ValueError('real reaped-child evidence missing')
        attempts=conn.execute('SELECT state,expense_actual,retail_actual,usage FROM provider_attempts WHERE job_id=ANY(%s)',([row[0] for row in jobs],)).fetchall()
        if len(attempts)<2 or any(row[0]!='settled' or row[1]!=0 or row[2]!=0 or not row[3] for row in attempts):
            raise ValueError('settled zero-cost real provider attempts required')
        journal=conn.execute('SELECT count(*) FROM engine_session_journal WHERE session_id=%s',(sid,)).fetchone()[0]
        pointer=conn.execute('SELECT published_thread_id,published_checkpoint_id FROM engine_sessions WHERE session_id=%s',(sid,)).fetchone()
        if not pointer or not pointer[0] or not pointer[1]:raise ValueError('published checkpoint pointer missing')
        checkpoints=conn.execute('SELECT count(*) FROM checkpoints WHERE thread_id=%s AND checkpoint_id=%s',(pointer[0],pointer[1])).fetchone()[0]
        if journal<2 or checkpoints<1:raise ValueError('retained journal/checkpoint output missing')
        outstanding=conn.execute("SELECT count(*) FROM provider_attempts WHERE job_id=ANY(%s) AND state IN ('reserved','dispatched','uncertain')",([row[0] for row in jobs],)).fetchone()[0]
        if outstanding:raise ValueError('provider work remains outstanding')
    return {'task_id':task_id,'child_jobs':len(jobs),'reaped_children':len(jobs),
            'settled_attempts':len(attempts),'journal_pages':journal,'checkpoints':checkpoints,
            'child-lifecycle':'passed','retained-outputs':'passed'}


if __name__=='__main__':
    try:print(json.dumps(inspect_task(sys.argv[1],stop_state=sys.argv[2:]==['--stop-state']),sort_keys=True))
    except Exception as error:raise SystemExit('retained image proof failed: '+type(error).__name__) from None
