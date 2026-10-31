"""Real account/registry checks, never a deployment-mode permission."""
import io,json,uuid,zipfile
import pytest
from fastapi.testclient import TestClient
from delivery_api.tenancy import tenant_context
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.operator_reads import OperatorReadStore


@pytest.fixture(autouse=True)
def clean_debug_registry():
    yield
    import psycopg
    from test_postgres_store import DSN
    with psycopg.connect(DSN) as conn:conn.execute("DELETE FROM operator_principals WHERE registry_version='debug-test'")


def setup(ws,monkeypatch,module):
    with ws.admin() as conn:
        conn.execute('GRANT requi_operator_read TO delivery_app')
        conn.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(ws.scope.owner_id,'debug-test',['debug.read']))
    reader=OperatorReadStore(ws.store._postgres.dsn,OperatorPrincipal(ws.scope.owner_id,'debug-test',frozenset({'debug.read'})))
    monkeypatch.setitem(module.app.state._state,'operator_read_store',reader)
    s=ws.store
    with tenant_context(ws.scope):
        tid=s.create_task('Account debug')['task_id'];s.set_engine_session(tid,str(uuid.uuid4()))
        job=s.enqueue_worker(tid,'phase:intent');cap=s.claim_worker(job)
        from delivery_api.generator_documents import GeneratorDocuments
        docs=GeneratorDocuments(s,cap,job)
        docs.write('interview/llm-calls.jsonl',b'{"event":"llm_call_end","provider":"fake","usage":{"prompt_tokens":12},"request":{"prompt":"project context"},"api_key":"private-secret"}\n')
        docs.write('interview/debug-http.jsonl',b'{"response":"raw project answer"}\n')
        s.finish_worker(job)
    return tid


def test_operator_debug_zip_and_execution_use_account_capability(pg_workspace_factory,pg_task_app,monkeypatch):
    import delivery_api.app as module
    with pg_workspace_factory() as ws:
        tid=setup(ws,monkeypatch,module);c=TestClient(pg_task_app(ws),headers=ws.headers)
        # Debug export must not depend on successful live engine status.
        monkeypatch.setattr(module,'get_task',lambda *a,**k:(_ for _ in ()).throw(AssertionError('engine must not be consulted')))
        r=c.post(f'/api/tasks/{tid}/debug.zip',json={})
        assert r.status_code==200,r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert b'project context' in z.read('interview/llm-calls.jsonl')
            assert b'private-secret' not in z.read('interview/llm-calls.jsonl')
            assert json.loads(z.read('manifest.json'))['task_id']==tid
        execution=c.get(f'/api/tasks/{tid}/execution?debug=1');assert execution.status_code==200
        assert execution.json()['raw']==[{'response':'raw project answer'}]
        assert execution.json()['calls'][0]['usage']['prompt_tokens']==12
        assert c.get('/api/auth').json()['debug_features'] is True
        with ws.admin() as conn:conn.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(ws.scope.owner_id,))
        assert c.post(f'/api/tasks/{tid}/debug.zip',json={}).status_code==404
        assert c.get(f'/api/tasks/{tid}/execution?debug=1').status_code==404
        assert c.get('/api/auth').json()['debug_features'] is False


def test_admin_badge_or_other_account_cannot_read_raw_debug(pg_workspace_factory,pg_task_app,monkeypatch):
    import delivery_api.app as module
    with pg_workspace_factory() as operator,pg_workspace_factory() as customer:
        setup(operator,monkeypatch,module)
        with tenant_context(customer.scope):
            tid=customer.store.create_task('Own customer task')['task_id'];customer.store.set_engine_session(tid,str(uuid.uuid4()))
        c=TestClient(pg_task_app(customer),headers=customer.headers)
        assert c.post(f'/api/tasks/{tid}/debug.zip',json={}).status_code==404
        assert c.get(f'/api/tasks/{tid}/execution?debug=1').status_code==404
        assert c.get('/api/auth').json()['debug_features'] is False


def test_debug_permission_is_not_admin_badge_and_does_not_depend_on_cloud_flag(pg_workspace_factory,pg_task_app,monkeypatch):
    import delivery_api.app as module
    from delivery_api.auth import Actor
    with pg_workspace_factory() as ws:
        tid=setup(ws,monkeypatch,module)
        for cloud in (True,False):
            monkeypatch.setattr(module.config,'CLOUD_TENANTS',cloud)
            with tenant_context(ws.scope):
                assert module.download_debug_zip(tid,module.BrowserDiagnostics(),Actor(ws.scope.owner_id,'operator',admin=False)).status_code==200
                assert module.task_execution(tid,True,Actor(ws.scope.owner_id,'operator',admin=False))['raw']
        with ws.admin() as conn:conn.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(ws.scope.owner_id,))
        from delivery_api.tenancy import TenantAccessDenied
        for cloud in (True,False):
            monkeypatch.setattr(module.config,'CLOUD_TENANTS',cloud)
            with tenant_context(ws.scope),pytest.raises(TenantAccessDenied):
                module.download_debug_zip(tid,module.BrowserDiagnostics(),Actor(ws.scope.owner_id,'admin',admin=True))


def test_operator_debug_zip_button_works_with_real_account_capability(pg_browser,monkeypatch):
    import delivery_api.app as module
    from playwright.sync_api import expect
    page,base,ws=pg_browser
    tid=setup(ws,monkeypatch,module)
    page.goto(base+'/tasks/'+tid+'/view')
    page.get_by_role('button',name='Account menu').click()
    page.get_by_role('menuitem',name='Debug',exact=True).click()
    button=page.locator('#downloaddebug');expect(button).to_be_enabled()
    with page.expect_download() as download:button.click()
    download.value.save_as('/downloads/account-debug-enabled.zip')
    expect(page.locator('#debugstatus')).to_contain_text('Download prepared')
    page.screenshot(path='/downloads/account-debug-enabled.png',full_page=False)
    with zipfile.ZipFile('/downloads/account-debug-enabled.zip') as z:
        assert b'project context' in z.read('interview/llm-calls.jsonl')
