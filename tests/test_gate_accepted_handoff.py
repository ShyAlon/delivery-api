"""A durable gate save must not wait for the next generator to finish."""
import asyncio
from types import SimpleNamespace

from delivery_api import app as module
from delivery_api import tenant_jobs
from delivery_api.tenancy import current_scope


def test_gate_response_returns_while_exact_next_job_is_still_running(monkeypatch,pg_scoped_workspace):
    scope=pg_scoped_workspace.scope
    gate={'state':'approved','revision':2}
    committed=[];started=[];release=None
    def save(*args):
        committed.append(True)
        return gate,'exact-job','architecture',None
    monkeypatch.setattr(module,'_decide_gate_sync',save)
    async def slow_dispatch(store,orchestrator,task_id,phase,*,queued_job_id):
        assert committed
        assert current_scope()==scope
        started.append((task_id,phase,queued_job_id))
        await release.wait()
    monkeypatch.setattr(tenant_jobs,'dispatch_phase',slow_dispatch)
    async def run():
        nonlocal release
        release=asyncio.Event()
        request=asyncio.create_task(module.decide_gate('task','gate-srs',module.GateDecision(action='approve'),None,SimpleNamespace(label='owner')))
        try:
            for _ in range(10):await asyncio.sleep(0)
            assert request.done(), 'HTTP save response is waiting for generator completion'
            assert await request==gate
            for _ in range(10):await asyncio.sleep(0)
            assert started==[('task','architecture','exact-job')]
        finally:
            release.set()
            await request
            for _ in range(10):await asyncio.sleep(0)
    asyncio.run(run())


def _real_gate(monkeypatch,workspace):
    from delivery_api.tenancy import tenant_context
    store=workspace.store
    with tenant_context(workspace.scope):
        tid=store.create_task('Gate HTTP handoff')['task_id']
        store.set_engine_session(tid,'gate-http-'+tid)
        store.set_model_default(tid,'deepseek:deepseek-flash')
        store.update_phase(tid,'needs','running')
        gate=store.wait_at_gate(tid,'needs')
    monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    # This fixture isolates transport/lifecycle from document validation, which
    # stays covered by test_gate_reviews and the transaction rollback suite.
    monkeypatch.setattr(module,'_recorded_engine_session',lambda *a:None)
    monkeypatch.setattr(module,'_task_artifact_snapshot',lambda *a:[{'phase_key':'needs','present':True,'stale':False,'valid':True}])
    return store,tid,gate


import pytest

@pytest.mark.parametrize('action',['approve','edit','redo'])
def test_real_pg_http_save_commits_once_before_worker_finishes(pg_browser,monkeypatch,action):
    import threading,time
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    store,tid,gate=_real_gate(monkeypatch,workspace)
    release=threading.Event();started=threading.Event();finished=threading.Event();calls=[]
    async def blocked(s,orchestrator,task_id,phase,*,queued_job_id):
        try:
            assert current_scope()==workspace.scope
            assert s._connection.get() is None
            job=s.worker_job(queued_job_id)
            assert job['state']=='pending' and job['task_id']==tid
            # A fresh connection can see the committed gate and queue already.
            from delivery_api.postgres_store import PostgresStore
            from psycopg.conninfo import conninfo_to_dict,make_conninfo
            independent=PostgresStore(make_conninfo(**{**conninfo_to_dict(workspace.dsn),'user':'delivery_app'}))
            assert independent.worker_job(queued_job_id)['job_id']==queued_job_id
            assert next(g for g in independent.task(tid)['gates'] if g['gate_key']==gate['gate_key'])['state']!='waiting'
            calls.append((task_id,phase,queued_job_id));started.set()
            while not release.is_set():await asyncio.sleep(.01)
        finally:finished.set()
    monkeypatch.setattr(tenant_jobs,'dispatch_phase',blocked)
    body={'action':action,'expected_revision':gate['revision']}
    body.update({'steer':'Fix this evidence'} if action=='redo' else {'edited_text':'Reviewed edit'} if action=='edit' else {'selected_option':'Proceed'})
    try:
        response=page.request.post(base+f'/api/tasks/{tid}/gates/{gate["gate_key"]}/decide',data=body,headers={"Origin":base},timeout=3000)
        assert response.status==200,response.text()
        assert started.wait(2)
        assert not finished.is_set()
        duplicate=page.request.post(base+f'/api/tasks/{tid}/gates/{gate["gate_key"]}/decide',data=body,headers={"Origin":base},timeout=3000)
        assert duplicate.status in (400,409)
        assert len(calls)==1
        assert calls[0][1]==('needs' if action=='redo' else 'srs')
        with tenant_context(workspace.scope):
            job=store.worker_job(calls[0][2])
            assert job['execution_options']['mode']==('redo' if action=='redo' else 'start')
    finally:
        release.set()
        if started.is_set():assert finished.wait(3)


def test_post_save_dispatch_failure_is_visible_without_reopening_gate(pg_browser,monkeypatch):
    import time
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    store,tid,gate=_real_gate(monkeypatch,workspace)
    async def fail(*args,**kwargs):raise RuntimeError('retained upstream document unavailable')
    monkeypatch.setattr(tenant_jobs,'dispatch_phase',fail)
    response=page.request.post(base+f'/api/tasks/{tid}/gates/{gate["gate_key"]}/decide',data={'action':'approve','selected_option':'Proceed','expected_revision':gate['revision']},headers={"Origin":base},timeout=3000)
    assert response.status==200,response.text()
    with tenant_context(workspace.scope):
        for _ in range(50):
            events=store.events(tid)
            if any(e['kind']=='error' for e in events):break
            time.sleep(.02)
        error=next(e for e in events if e['kind']=='error')
        assert 'retained upstream document unavailable' in error['message']
        assert error['payload']['gate_decision'] is True
        task=store.task(tid)
        assert next(g for g in task['gates'] if g['gate_key']==gate['gate_key'])['state']=='decided'
        assert next(p for p in task['phases'] if p['phase_key']=='srs')['state']=='failed'
