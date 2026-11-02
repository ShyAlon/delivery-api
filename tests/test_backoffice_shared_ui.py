from pathlib import Path as _EvidencePath
import os as _evidence_os
_EVIDENCE=_EvidencePath(_evidence_os.environ.get('REQUI_TEST_ARTIFACTS','/downloads'));_EVIDENCE.mkdir(parents=True,exist_ok=True)
"""Shared assets, navigation, menus and diagnostics without live accounts or providers."""
import socket,threading,time
import httpx,pytest,uvicorn
from fastapi.testclient import TestClient
from delivery_api.backoffice import create_app

@pytest.fixture
def ui():
    calls=[]
    def upstream(request):
        calls.append(request)
        if request.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{c:True for c in ('services.operate','logs.read','debug.read','sessions.read','analytics.read','usage.read','payments.read')}})
        if request.url.path=='/api/operations/active-jobs':
            return httpx.Response(200,json={'scope':'current_authorized_workspace','mutations_available':False,'jobs':[{'title':'Shared UI test','task_id':'task-one','job_id':'job-one','kind':'phase:intent','state':'running','task_state':'running','generation':1,'task_generation':1,'updated_at':1}],'truncated':False})
        if request.url.path.endswith('/expense-totals'):
            return httpx.Response(200,json={'scope':'reviewed_cross_user_operator_read','operator_subject':'fixture','registry_version':'fixture','as_of':123,'rolling_settled_units':0,'outstanding_hold_units':30,'conservative_used_units':30,'uncertain_attempts':1})
        if request.url.path.endswith('/workspace'):
            return httpx.Response(200,json={'scope':'current_authorized_workspace','sessions':[],'credits':None,'reason':'No account'})
        if request.url.path.endswith('/orphaned-tasks'):
            return httpx.Response(200,json={'tasks':[]})
        return httpx.Response(403,json={'detail':'unavailable'})
    app=create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
    thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        assert server.started
        yield f'http://127.0.0.1:{port}',calls
    finally:server.should_exit=True;thread.join(timeout=10)

def test_shared_assets_and_csp_keep_authority_on_exact_proxy():
    client=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(lambda r:httpx.Response(500))))
    shell=client.get('/')
    assert "script-src 'self';" in shell.headers['content-security-policy']
    assert '<script>' not in shell.text
    for asset in ['/assets/fonts.css','/assets/style.css','/assets/chrome.css','/assets/client-core.js','/assets/ui-action.js','/assets/ops-table.js','/assets/operator-ui.js','/backoffice-assets/backoffice.js','/backoffice-assets/backoffice.css','/favicon.ico']:
        assert client.get(asset).status_code==200,asset
    assert client.post('/api/client-log',json={}).status_code==404
    assert client.post('/api/operations/workspace',json={}).status_code==405
    assert client.get('/assets/../../backoffice.py').status_code==404


def test_tabs_keyboard_context_menu_and_local_debug(ui):
    from playwright.sync_api import sync_playwright,expect
    url,calls=ui
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':1000},extra_http_headers={'Authorization':'Bearer fake'})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url);row=page.locator('#active-jobs tbody tr').first;row.wait_for()
        assert page.get_by_role('tab').all_text_contents()==['Worker Jobs','Workspace','Accounts','Spend','Model pins','Payments','Audit','Debug']
        assert page.locator('[role=tabpanel]').count()==13
        for tab,panel in [('Workspace','workspace-panels'),('Accounts','accounts-panel'),('Spend','expense-panel'),('Payments','payments-panel'),('Debug','debug-panel'),('Worker Jobs','active-jobs-panel')]:
            page.get_by_role('tab',name=tab,exact=True).click();expect(page.locator('#'+panel)).to_be_visible()
            expect(page.get_by_role('tab',name=tab,exact=True)).to_have_attribute('aria-selected','true')
            assert page.locator('[role=tabpanel]:visible').count() in (1,2,3)
        tab=page.get_by_role('tab',name='Worker Jobs',exact=True);tab.focus();page.keyboard.press('End');expect(page.locator('#debug-panel')).to_be_visible();page.keyboard.press('Home');expect(page.locator('#active-jobs-panel')).to_be_visible()
        row.click(button='right');assert page.get_by_role('menuitem').all_text_contents()==['View','Stop','Delete']
        page.keyboard.press('ArrowDown');expect(page.get_by_role('menuitem',name='Stop',exact=True)).to_be_focused()
        page.keyboard.press('Escape');expect(page.get_by_role('button',name='Job actions')).to_be_focused();assert not page.get_by_role('menu').count()
        row.focus();page.keyboard.press('Shift+F10');page.get_by_role('menuitem',name='View',exact=True).click();expect(page.locator('.detail')).to_be_visible()
        assert not [r for r in calls if r.method=='POST']
        assert page.get_by_role('button',name='Job actions').inner_text()=='⋯'
        page.get_by_role('button',name='Job actions').click();page.get_by_role('menuitem',name='View',exact=True).click();assert not page.locator('.detail').count()
        page.get_by_role('tab',name='Debug',exact=True).click();page.locator('#debug-refresh').click()
        expect(page.locator('#debug-report')).to_contain_text('/api/operations/active-jobs')
        expect(page.locator('#debug-report')).to_contain_text('backoffice.ready')
        page.screenshot(path=str(_EVIDENCE/'backoffice-shared-debug.png'),full_page=True)
        page.get_by_role('tab',name='Worker Jobs',exact=True).click();page.locator('#active-jobs-refresh').click();row.wait_for()
        page.get_by_role('button',name='Job actions').click();page.screenshot(path=str(_EVIDENCE/'backoffice-shared-menu-desktop.png'),full_page=True);page.keyboard.press('Escape')
        page.set_viewport_size({'width':390,'height':844});page.emulate_media(reduced_motion='reduce');row.scroll_into_view_if_needed();row.click(button='right');page.screenshot(path=str(_EVIDENCE/'backoffice-shared-menu-mobile.png'),full_page=False)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.evaluate("getComputedStyle(document.querySelector('.panel')).animationName")=='none'
        page.keyboard.press('Escape');page.goto(url+'/#payments');expect(page.locator('#payments-panel')).to_be_visible();assert not errors
        browser.close()


def test_uncertain_provider_warning_is_visible_without_mutation(ui):
    from playwright.sync_api import sync_playwright,expect
    url,calls=ui
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':950},extra_http_headers={'Authorization':'Bearer fake'})
        page.goto(url+'/#spend')
        expect(page.locator('#operator-status')).to_contain_text('not an admission block')
        expect(page.locator('#operator-status')).to_contain_text('Do not retry the same call')
        expect(page.locator('#operator-status')).to_have_class('accounting-warning')
        expect(page.locator('#expense')).to_contain_text('Uncertain attempts: 1')
        page.screenshot(path=str(_EVIDENCE/'uncertainty-backoffice-warning.png'),full_page=True)
        assert not [r for r in calls if r.method=='POST']
        browser.close()


def test_audit_table_dialog_binary_hide_and_cross_owner_guard(ui):
    import json
    from playwright.sync_api import sync_playwright,expect
    url,calls=ui
    def item(n,own=True):
        return {'id':n,'ts':123,'event':'provider.attempt.uncertain','actor':'Operator','owner_id':'owner-one' if own else 'owner-two','task_id':'task-one','detail':{'outcome':'unknown','admission':'warning_only'},'current_owner':own,'attempt_id':'attempt-one','job_id':'job-one','attempt_state':'uncertain','job_state':'failed','task_hidden':False,'actions':{'hide':{'available':own,'reason':'Audit and accounting evidence retained.'},'cancel':{'available':False,'reason':'Not reserved.'},'release':{'available':False,'reason':'Unknown dispatch is not proof of no cost.'}}}
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':1000},extra_http_headers={'Authorization':'Bearer fake'})
        mutations=[]
        def events(route):
            before='before=' in route.request.url
            route.fulfill(json={'scope':'reviewed_cross_user_operator_read','events':[item(1,False)] if before else [item(3),item(2)],'truncated':not before,'next_before':None if before else 2,'event_types':['provider.attempt.uncertain']})
        page.route('**/api/operations/audit-events?*',events)
        def resolve(route):
            mutations.append(route.request.post_data_json)
            route.fulfill(json={'resolved':True,'accounting_retained':True})
        page.route('**/api/operations/audit-events/*/resolve',resolve)
        page.goto(url+'/#audit')
        expect(page.locator('#audit-panel tbody tr')).to_have_count(2)
        page.get_by_role('button',name='Next audit page').click();expect(page.locator('#audit-panel tbody tr')).to_have_count(3)
        page.get_by_role('row',name='Open audit event 1',exact=True).click()
        expect(page.locator('.audit-dialog')).to_contain_text('Other-owner row: evidence only')
        expect(page.get_by_role('button',name='Delete task (hide)',exact=True)).to_be_disabled()
        page.get_by_role('button',name='Close',exact=True).click()
        page.get_by_label('Filter audit',exact=True).fill('owner-one');expect(page.locator('#audit-panel tbody tr')).to_have_count(2)
        page.get_by_role('row',name='Open audit event 3',exact=True).click()
        expect(page.get_by_role('button',name='Release',exact=True)).to_be_disabled()
        page.screenshot(path=str(_EVIDENCE/'backoffice-audit-evidence.png'),full_page=True)
        page.get_by_role('button',name='Delete task (hide)',exact=True).click()
        expect(page.get_by_role('dialog').last).to_contain_text('Accounting and audit records stay')
        page.get_by_role('button',name='Cancel',exact=True).click();assert not mutations
        page.get_by_role('button',name='Delete task (hide)',exact=True).click()
        page.get_by_role('button',name='Delete task',exact=True).click()
        expect(page.locator('.audit-dialog [role=status]')).to_contain_text('Success:')
        assert mutations==[{'action':'hide','confirm':True,'expected_attempt_state':'uncertain'}]
        page.screenshot(path=str(_EVIDENCE/'backoffice-audit-resolution.png'),full_page=True)
        browser.close()
