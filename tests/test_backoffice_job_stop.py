"""Confirmed owner Stop+hide retains running and unsettled evidence."""
import uuid
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B


def test_stop_hide_is_confirmed_owner_scoped_and_does_not_invent_exit(store):
    with tenant_context(A):
        task=store.create_task('Confirmed operator cleanup '+uuid.uuid4().hex)
        store.set_engine_session(task['task_id'],str(uuid.uuid4()))
        job=store.enqueue_worker(task['task_id'],'phase:intent');assert store.claim_worker(job)
        before=store.task(task['task_id'])
        with pytest.raises(ValueError,match='confirmation'):store.stop_hide_worker_task(job,confirm=False)
        assert store.task(task['task_id'])['generation']==before['generation']
    with tenant_context(B),pytest.raises(KeyError):store.stop_hide_worker_task(job,confirm=True)
    with tenant_context(A):
        result=store.stop_hide_worker_task(job,confirm=True)
        assert result['hidden'] and result['stop_requested']
        assert result['worker_exit_verified'] is False and result['accounting_settled'] is False
        assert store.task(task['task_id']) is None
        assert store.worker_job(job)['state']=='running'
        assert job not in {x['job_id'] for x in store.owner_active_jobs()['jobs']}
        assert store.stop_hide_worker_task(job,confirm=True)['already_hidden']
        with store._postgres.transaction() as conn:
            row=conn.execute('SELECT generation,stop_requested_at,deleted_at,stopped_at FROM tasks WHERE task_id=%s',(task['task_id'],)).fetchone()
        assert row['generation']==before['generation']+1 and row['stop_requested_at'] and row['deleted_at']
        assert row['stopped_at'] is None


def test_proxy_confirm_requires_same_origin_and_fixed_body():
    import httpx
    from fastapi.testclient import TestClient
    from delivery_api.backoffice import create_app
    calls=[]
    def upstream(request):
        calls.append(request);return httpx.Response(200,json={'hidden':True,'stop_requested':True,'accounting_retained':True})
    c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    route='/api/operations/worker-jobs/job-1/stop'
    headers={'Authorization':'Bearer fake','Origin':'http://testserver'}
    assert c.post(route,json={'confirm':True},headers={'Authorization':'Bearer fake'}).status_code==403
    assert c.post(route,json={'confirm':False},headers=headers).status_code==422
    assert c.post(route,json={'confirm':True,'owner_id':'foreign'},headers=headers).status_code==422
    assert c.post(route,json={'confirm':True},headers=headers).status_code==200
    assert len(calls)==1 and calls[0].method=='POST' and calls[0].url.path==route
    assert calls[0].headers['origin']=='http://127.0.0.1:8000'


def test_accepted_provider_accounting_survives_stop_and_hide(pg_workspace_factory):
    from test_attempt_ledger import Q
    from contextlib import ExitStack
    with ExitStack() as stack:
        workspace=stack.enter_context(pg_workspace_factory())
        store=workspace.store
        stack.enter_context(tenant_context(workspace.scope))
        task=store.create_task('Retained accounting')
        tid=task['task_id'];store.set_engine_session(tid,str(uuid.uuid4()))
        job=store.enqueue_worker(tid,'phase:needs');store.claim_worker(job)
        with workspace.admin() as c:c.execute('INSERT INTO admission_limits VALUES (%s,%s,1000,300,700,300,700,60,100,2)',(workspace.scope.owner_id,'test-only'))
        attempt=uuid.uuid4().hex
        store.reserve_provider_attempt(job,attempt,Q,'reviewed-fake-request')
        store.transition_provider_attempt(attempt,'start')
        store.stop_hide_worker_task(job,confirm=True)
        assert store.provider_attempt(attempt)['state']=='dispatched'
        assert store.worker_job(job)['state']=='running'
        store.transition_provider_attempt(attempt,'settle',{'input_tokens':1,'output_tokens':1})
        assert store.provider_attempt(attempt)['state']=='settled'


def test_confirmed_hidden_running_job_no_longer_blocks_profile_transition(pg_workspace_factory):
    from delivery_api.own_test_transition import transition,SCALE
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        task=store.create_task('Profile cleanup')
        store.set_engine_session(task['task_id'],str(uuid.uuid4()))
        job=store.enqueue_worker(task['task_id'],'phase:intent');store.claim_worker(job)
        with workspace.admin() as c:
            c.execute('INSERT INTO admission_limits VALUES (%s,%s,86400,%s,NULL,NULL,NULL,60,NULL,3)',(workspace.scope.owner_id,'test-reviewed',2*SCALE))
            email=c.execute('SELECT email FROM auth.users WHERE id=%s',(workspace.scope.owner_id,)).fetchone()[0]
            with pytest.raises(Exception,match='stop and reconcile'):transition(c,email)
        store.stop_hide_worker_task(job,confirm=True)
        with workspace.admin() as c:
            result=transition(c,email,apply=True)
            assert result['new_expense_limit'] is None
        assert store.worker_job(job)['state']=='running'


def test_stop_http_requires_account_registry_and_confirms_even_engine_gone(pg_workspace_factory,pg_task_app,monkeypatch):
    from fastapi.testclient import TestClient
    from delivery_api import app as service
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.operator_reads import OperatorReadStore
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        task=store.create_task('HTTP confirmed cleanup')
        store.set_engine_session(task['task_id'],str(uuid.uuid4()))
        job=store.enqueue_worker(task['task_id'],'phase:intent');store.claim_worker(job)
        principal=OperatorPrincipal(workspace.scope.owner_id,'stop-test',frozenset({'debug.read'}))
        reader=OperatorReadStore(workspace.dsn.replace('user=sandbox','user=delivery_app'),principal)
        monkeypatch.setitem(service.app.state._state,'operator_read_store',reader)
        monkeypatch.setattr(service,'cloud_execution_admitted',lambda:False)
        class Gone:
            def stop(self,sid):raise RuntimeError('engine gone')
        monkeypatch.setattr(service,'engine_client',Gone())
        client=TestClient(pg_task_app(workspace));path='/api/operations/worker-jobs/'+job+'/stop'
        with workspace.admin() as c:
            c.execute('GRANT requi_operator_read TO delivery_app')
            c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(workspace.scope.owner_id,'stop-test',['debug.read']))
        try:
            assert client.post(path,json={'confirm':False},headers=workspace.headers).status_code==422
            got=client.post(path,json={'confirm':True},headers=workspace.headers)
            assert got.status_code==200,got.text
            assert got.json()['hidden'] and not got.json()['engine_notification_verified']
            assert store.worker_job(job)['state']=='running'
            with workspace.admin() as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(workspace.scope.owner_id,))
            assert client.post(path,json={'confirm':True},headers=workspace.headers).status_code==404
        finally:
            with workspace.admin() as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(workspace.scope.owner_id,))


@__import__('pytest').mark.parametrize('hide',[False,True])
def test_browser_confirm_sends_one_scoped_stop_and_refreshes(tmp_path,hide):
    import httpx,socket,threading,time,uvicorn
    from playwright.sync_api import sync_playwright
    from delivery_api.backoffice import create_app
    stopped=[False];calls=[]
    def upstream(request):
        calls.append(request)
        if request.method=='POST':
            assert request.url.path=='/api/operations/worker-jobs/job-one/stop'
            assert __import__('json').loads(request.content)=={'confirm':True,'hide':hide}
            stopped[0]=True;return httpx.Response(200,json={'hidden':hide,'stop_requested':True,'accounting_retained':True,'accounting_settled':False,'worker_exit_verified':False})
        if request.url.path=='/api/operations/active-jobs':
            row={'title':'Confirm cleanup','task_id':'task-one','job_id':'job-one','kind':'phase:intent','state':'running','task_state':'running','generation':1,'task_generation':1,'stop_requested_at':None,'updated_at':1}
            return httpx.Response(200,json={'scope':'current_authorized_workspace','jobs':[] if stopped[0] else [row],'mutations_available':False,'truncated':False,'next_before':None})
        return httpx.Response(403,json={'detail':'unavailable'})
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)),host='127.0.0.1',port=port,lifespan='off',log_level='error'))
    thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(extra_http_headers={'Authorization':'Bearer fake'})
            page.goto(f'http://127.0.0.1:{port}/');page.locator('#active-jobs tbody tr').wait_for()
            page.get_by_role('button',name='Job actions',exact=True).click()
            page.get_by_role('menuitem',name='Delete' if hide else 'Stop',exact=True).click()
            page.get_by_role('button',name='Stop and delete' if hide else 'Stop task',exact=True).click()
            page.get_by_text('No pending, running or paused jobs returned.',exact=True).wait_for()
            assert len([x for x in calls if x.method=='POST'])==1
            assert not page.get_by_role('dialog').count()
            browser.close()
    finally:server.should_exit=True;thread.join(timeout=10)


def test_stop_only_keeps_task_and_saved_work_visible(pg_workspace_factory):
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store;task=store.create_task('Stop without Delete')
        store.set_engine_session(task['task_id'],str(uuid.uuid4()))
        job=store.enqueue_worker(task['task_id'],'phase:intent');store.claim_worker(job)
        result=store.stop_hide_worker_task(job,confirm=True,hide=False)
        assert result['hidden'] is False and result['stop_requested']
        assert store.task(task['task_id'])['state']=='stop_requested'
        assert store.worker_job(job)['state']=='running'
        assert job in {x['job_id'] for x in store.owner_active_jobs()['jobs']}
        result=store.stop_hide_worker_task(job,confirm=True,hide=True)
        assert result['hidden'] and store.task(task['task_id']) is None
