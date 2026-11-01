"""Narrow force proxy and browser review contract."""
import httpx
from fastapi.testclient import TestClient
from delivery_api.backoffice import create_app


def test_force_proxy_requires_same_origin_exact_body_and_signin():
    calls=[]
    def upstream(request):
        calls.append(request);return httpx.Response(200,json={'forced_count':1,'results':[{'worker_exit_verified':False}],'warning':'Unverified'})
    c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    path='/api/operations/force-stop-all';body={'fingerprint':'a'*64,'confirmation':'FORCE STOP 1 JOBS'}
    assert c.post(path,json=body).status_code==403
    headers={'Origin':'http://testserver','Authorization':'Bearer fake'}
    assert c.post(path,json=body|{'owner_id':'foreign'},headers=headers).status_code==422
    assert c.post(path,json=body|{'confirmation':'yes'},headers=headers).status_code==422
    assert c.post(path,json=body,headers={'Origin':'http://testserver'}).status_code==401
    assert c.post(path,json=body,headers=headers).status_code==200
    assert len(calls)==1 and calls[0].url.path==path
    assert calls[0].headers['origin']=='http://127.0.0.1:8000'


def test_force_plan_is_read_only_fixed_route():
    calls=[]
    def upstream(request):calls.append(request);return httpx.Response(200,json={'jobs':[],'accounting':[]})
    c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    headers={'Authorization':'Bearer fake'}
    assert c.get('/api/operations/force-stop-plan?owner_id=foreign',headers=headers).status_code==422
    assert c.get('/api/operations/force-stop-plan',headers=headers).status_code==200
    assert calls[0].method=='GET' and calls[0].url.path=='/api/operations/force-stop-plan'


def test_browser_force_reviews_hidden_jobs_requires_phrase_and_sends_once(tmp_path):
    import socket,threading,time,uvicorn,json
    from playwright.sync_api import sync_playwright
    calls=[]
    def upstream(request):
        calls.append(request)
        if request.url.path=='/api/operations/force-stop-plan':
            return httpx.Response(200,json={'owner_id':'owner-one','fingerprint':'a'*64,'warning':'Worker exit UNVERIFIED. Accounting unchanged. Accepted calls may bill.',
              'jobs':[{'job_id':'hidden-job','title':'Meditator 7.5','task_id':'hidden-task','state':'running','deleted_at':1}],
              'accounting':[{'ledger':'provider','id':'attempt-one','state':'uncertain'}]})
        if request.method=='POST':
            assert json.loads(request.content)=={'fingerprint':'a'*64,'confirmation':'FORCE STOP 1 JOBS'}
            return httpx.Response(200,json={'forced_count':1,'results':[{'job_id':'hidden-job','state':'failed','worker_exit_verified':False}],
              'warning':'Worker exit UNVERIFIED. Accounting unchanged.','accounting':[{'state':'uncertain'}]})
        if request.url.path=='/api/operations/active-jobs':return httpx.Response(200,json={'scope':'current_authorized_workspace','jobs':[],'mutations_available':False,'truncated':False})
        return httpx.Response(403,json={'detail':'unavailable'})
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)),host='127.0.0.1',port=port,lifespan='off',log_level='error'))
    thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1100,'height':850},extra_http_headers={'Authorization':'Bearer fake'})
            page.goto(f'http://127.0.0.1:{port}/')
            page.get_by_role('button',name='Review force stop-all').click()
            page.get_by_role('dialog').wait_for()
            assert page.get_by_role('button',name='Force close job records').is_disabled()
            assert 'hidden-job' in page.locator('.force-plan').inner_text() and 'uncertain' in page.locator('.force-plan').inner_text()
            page.screenshot(path='/downloads/requi-rollout/force-review-desktop.png')
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path='/downloads/requi-rollout/force-review-mobile.png')
            page.locator('#force-stop-phrase').fill('FORCE STOP 1 JOBS')
            page.get_by_role('button',name='Force close job records').click()
            page.get_by_text('1 job records forced closed.',exact=False).wait_for()
            assert len([x for x in calls if x.method=='POST'])==1
            assert 'uncertain' in page.locator('.force-plan').inner_text()
            browser.close()
    finally:server.should_exit=True;thread.join(timeout=10)


def test_service_force_requires_account_debug_and_uses_authenticated_workspace(pg_workspace_factory,pg_task_app,monkeypatch):
    import uuid
    from delivery_api.tenancy import tenant_context
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api import app as service
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store;task=store.create_task('HTTP force')
        store.set_engine_session(task['task_id'],str(uuid.uuid4()))
        job=store.enqueue_worker(task['task_id'],'phase:intent');store.claim_worker(job)
        store.stop_hide_worker_task(job,confirm=True)
        reader=OperatorReadStore(workspace.dsn.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(workspace.scope.owner_id,'force-test',frozenset({'debug.read'})))
        monkeypatch.setitem(service.app.state._state,'operator_read_store',reader)
        client=TestClient(pg_task_app(workspace))
        with workspace.admin() as c:
            c.execute('GRANT requi_operator_read TO delivery_app')
            c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(workspace.scope.owner_id,'force-test',['debug.read']))
        plan=client.get('/api/operations/force-stop-plan',headers=workspace.headers)
        assert plan.status_code==200,plan.text
        review=plan.json();assert job in {r['job_id'] for r in review['jobs']}
        body={'fingerprint':review['fingerprint'],'confirmation':'FORCE STOP '+str(len(review['jobs']))+' JOBS'}
        assert client.post('/api/operations/force-stop-all',json=body|{'owner_id':'foreign'},headers=workspace.headers).status_code==422
        got=client.post('/api/operations/force-stop-all',json=body,headers=workspace.headers)
        assert got.status_code==200,got.text
        assert store.worker_job(job)['state']=='failed' and not got.json()['results'][0]['worker_exit_verified']
        with workspace.admin() as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(workspace.scope.owner_id,))
        assert client.get('/api/operations/force-stop-plan',headers=workspace.headers).status_code==404
