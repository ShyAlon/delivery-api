from pathlib import Path as _EvidencePath
import os as _evidence_os
_EVIDENCE=_EvidencePath(_evidence_os.environ.get('REQUI_TEST_ARTIFACTS','/downloads'));_EVIDENCE.mkdir(parents=True,exist_ok=True)
"""Owner-wide job diagnostics, without mutation or inferred stale status."""
import httpx
from fastapi.testclient import TestClient
from delivery_api.backoffice import create_app
from delivery_api.tenancy import tenant_context
from test_postgres_store import A


def test_active_jobs_proxy_is_read_only_and_preserves_exact_pagination():
    calls=[]
    def upstream(request):
        calls.append(request)
        return httpx.Response(200,json={'scope':'current_authorized_workspace','jobs':[],'truncated':False})
    c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream)))
    assert c.get('/api/operations/active-jobs').status_code==401
    headers={'Authorization':'Bearer fake'}
    got=c.get('/api/operations/active-jobs?limit=50&before_updated_at=123&before_job_id=job',headers=headers)
    assert got.status_code==200
    assert calls[0].url.path=='/api/operations/active-jobs'
    assert calls[0].url.params['before_job_id']=='job'
    assert c.post('/api/operations/active-jobs',headers=headers).status_code==405
    assert c.get('/api/operations/active-jobs?owner_id=foreign',headers=headers).status_code==422
    assert len(calls)==1


def test_nonterminal_jobs_include_title_and_page_without_hiding_old_blockers(pg_workspace_factory):
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        task=store.create_task('Named maintenance blocker')
        store.set_engine_session(task['task_id'],__import__('uuid').uuid4().hex)
        job=store.enqueue_worker(task['task_id'],'phase:intent')
        claimed=store.claim_worker(job)
        assert claimed
        other=store.create_task('Newer pending maintenance')
        store.set_engine_session(other['task_id'],__import__('uuid').uuid4().hex)
        pending=store.enqueue_worker(other['task_id'],'phase:intent')
        page=store.owner_active_jobs(limit=1)
        assert len(page['jobs'])==1 and page['truncated']
        ids={page['jobs'][0]['job_id']}
        while page['truncated']:
            page=store.owner_active_jobs(limit=1,before=page['next_before'])
            ids|={x['job_id'] for x in page['jobs']}
        assert {job,pending}<=ids
        rows=store.owner_active_jobs(limit=100)['jobs']
        row=next(x for x in rows if x['job_id']==job)
        assert row['title']=='Named maintenance blocker' and row['state']=='running'
        assert row['task_id']==task['task_id']
        assert row['quiescence_verified'] is False and row['stale_verified'] is False
        store.pause_interrupted_worker(job)
        assert next(x for x in store.owner_active_jobs()['jobs'] if x['job_id']==job)['state']=='paused'
        store.reauthorize_worker(job)
        assert store.claim_worker(job)
        store.finish_worker(job)
        assert job not in {x['job_id'] for x in store.owner_active_jobs()['jobs']}


def test_service_requires_live_account_debug_and_never_accepts_another_owner(pg_workspace_factory,pg_task_app,monkeypatch):
    import uuid
    from delivery_api import app as service
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.operator_reads import OperatorReadStore
    with pg_workspace_factory() as first,pg_workspace_factory() as second:
        jobs=[]
        for workspace in (first,second):
            with tenant_context(workspace.scope):
                task=workspace.store.create_task('Owner-only title')
                workspace.store.set_engine_session(task['task_id'],str(uuid.uuid4()))
                jobs.append(workspace.store.enqueue_worker(task['task_id'],'phase:intent'))
        reader=OperatorReadStore(first.dsn.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(first.scope.owner_id,'active-jobs-test',frozenset({'debug.read'})))
        monkeypatch.setitem(service.app.state._state,'operator_read_store',reader)
        with first.admin() as c:
            c.execute('GRANT requi_operator_read TO delivery_app')
            c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(first.scope.owner_id,'active-jobs-test',['debug.read']))
        try:
            client=TestClient(pg_task_app(first))
            assert client.get('/api/operations/active-jobs').status_code==401
            got=client.get('/api/operations/active-jobs',headers=first.headers)
            assert got.status_code==200,got.text
            assert got.headers['cache-control']=='no-store'
            assert {x['job_id'] for x in got.json()['jobs']}=={jobs[0]}
            assert client.get('/api/operations/active-jobs',headers=second.headers).status_code==404
            assert client.get('/api/operations/active-jobs?before_job_id=x',headers=first.headers).status_code==422
            assert client.get('/api/operations/active-jobs?limit=0',headers=first.headers).status_code==422
            with first.admin() as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(first.scope.owner_id,))
            assert client.get('/api/operations/active-jobs',headers=first.headers).status_code==404
        finally:
            with first.admin() as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(first.scope.owner_id,))


def test_backoffice_jobs_pixels_filter_pagination_refresh_and_revocation(tmp_path):
    import socket,threading,time,uvicorn
    from playwright.sync_api import sync_playwright
    calls=[];revoked=[False]
    def upstream(request):
        if request.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{'debug.read':True,'logs.read':True}})
        calls.append(request)
        if request.url.path!='/api/operations/active-jobs':return httpx.Response(403,json={'detail':'unavailable'})
        if revoked[0]:return httpx.Response(401,json={'detail':'revoked'})
        older='before_job_id' in request.url.params
        row={'title':'Meditator 7.5' if not older else 'Older pending task','task_id':'77c30a258523' if not older else 'older-task','job_id':'ee89bcb759964a795ed7cf827871eb86' if not older else 'older-job','kind':'phase:intent','state':'running' if not older else 'pending','task_state':'stop_requested' if not older else 'running','generation':1,'task_generation':2,'stop_requested_at':123 if not older else None,'updated_at':1793383200,'quiescence_verified':False,'stale_verified':False}
        return httpx.Response(200,json={'scope':'current_authorized_workspace','jobs':[row],'mutations_available':False,'truncated':not older,'next_before':None if older else {'updated_at':1793383200,'job_id':row['job_id']}})
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
    thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1280,'height':1000},extra_http_headers={'Authorization':'Bearer fake'})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}/')
            page.locator('#active-jobs tbody tr').wait_for()
            assert 'Meditator 7.5' in page.locator('#active-jobs').inner_text()
            assert 'ee89bcb759964a795ed7cf827871eb86' in page.locator('#active-jobs').inner_text()
            assert page.locator('#active-jobs-panel button').count()==3 # refresh/next plus shared row menu
            page.get_by_role('button',name='Job actions',exact=True).click()
            assert page.get_by_role('menuitem').all_text_contents()==['View','Stop','Delete']
            page.get_by_role('menuitem',name='Stop',exact=True).click()
            page.get_by_role('dialog').wait_for()
            assert 'accepted provider call' in page.get_by_role('dialog').inner_text()
            page.screenshot(path=str(_EVIDENCE/'backoffice-worker-jobs-confirm.png'),full_page=False)
            page.get_by_role('button',name='Cancel',exact=True).click()
            page.locator('#active-jobs-filter').fill('no-match')
            assert page.locator('#active-jobs').is_hidden()
            page.locator('#active-jobs-filter').fill('77c30a258523')
            assert page.locator('#active-jobs').is_visible()
            page.screenshot(path=str(_EVIDENCE/'backoffice-owner-jobs-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(_EVIDENCE/'backoffice-owner-jobs-mobile.png'),full_page=True)
            page.locator('#active-jobs-filter').fill('')
            page.locator('#active-jobs-next').click()
            page.get_by_text('Older pending task / older-task',exact=True).wait_for()
            page.locator('#active-jobs-refresh').click()
            page.get_by_text('Meditator 7.5 / 77c30a258523',exact=True).wait_for()
            revoked[0]=True;page.locator('#active-jobs-refresh').click()
            page.locator('#active-jobs-empty').filter(has_text='Sign in').wait_for()
            assert 'Meditator' not in page.locator('#active-jobs').inner_text()
            assert not errors
            assert all(r.method=='GET' for r in calls)
            browser.close()
    finally:server.should_exit=True;thread.join(timeout=10)
