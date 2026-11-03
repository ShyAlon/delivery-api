from test_artifacts import evidence_path
from test_ui_playwright import pg_ui,pg_page,pg_ids,_promote_control
from playwright.sync_api import expect


def test_failed_tenant_interview_explains_unavailable_rerun_without_confirm(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    live=page.request.get(ui+'/api/tasks/'+tid).json()
    live.update({'state':'failed','active_phase':'intent','simulated':False,
        'input_state':{'waiting':False,'process_live':False},
        'ui_state':{'mode':'error','active_phase':'intent','process_live':False},
        'rerun_control':{'available':False,'reason':'Tenant stage rerun is unavailable: new-generation session/checkpoint recovery is not implemented.'}})
    next(p for p in live['phases'] if p['phase_key']=='intent')['state']='failed'
    live['events'].append({'id':9999,'kind':'error','message':'Interview failed','phase_key':'intent','payload':{},'ts':1720004001})
    page.route('**/api/tasks/'+tid,lambda r:r.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
    page.goto(ui+'/tasks/'+tid+'/view')
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.locator('#rerununavailable')).to_contain_text('new-generation')
    expect(page.locator('#rerununavailable')).to_contain_text('Audit')
    page.screenshot(path=evidence_path('tenant-rerun-unavailable.png'),full_page=True)


import pytest

@pytest.mark.parametrize('phase_key,button',[('needs','retryneeds'),('srs','retryphase')])
def test_tenant_supported_generator_retry_stays_available(pg_page,pg_ui,pg_ids,phase_key,button):
    page,ui=pg_page,pg_ui
    tid=pg_ids["live"]
    live=page.request.get(ui+'/api/tasks/'+tid).json()
    found=False
    for phase in live['phases']:
        if phase['phase_key']==phase_key:
            phase['state']='failed';found=True
        else:phase['state']='pending' if found else 'approved'
    live.update({'state':'failed','active_phase':phase_key,'simulated':False,
        'input_state':{'waiting':False,'process_live':False,'engine_process_status':40},
        'ui_state':{'mode':'error','active_phase':phase_key,'process_live':False},
        'rerun_control':{'available':False,'reason':'Tenant stage rerun is unavailable: new-generation session/checkpoint recovery is not implemented.'}})
    page.route('**/api/tasks/'+tid,lambda r:r.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
    page.goto(ui+'/tasks/'+tid+'/view')
    expect(page.locator('#'+button)).to_be_visible()
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.locator('#rerununavailable')).to_contain_text('Use the phase Retry')


def test_healthy_start_has_no_recovery_warning_and_headers_never_overlap(pg_page,pg_ui,pg_ids):
    page,ui=pg_page,pg_ui;tid=pg_ids['live']
    live=page.request.get(ui+'/api/tasks/'+tid).json()
    live.update({'state':'running','active_phase':'intent','simulated':False,
        'input_state':{'waiting':False,'process_live':True},
        'ui_state':{'mode':'executing','active_phase':'intent','process_live':True},
        'rerun_control':{'available':False,'reason':'Tenant stage rerun is unavailable.'}})
    page.route('**/api/tasks/'+tid,lambda r:r.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
    page.set_viewport_size({'width':1280,'height':900});page.goto(ui+'/tasks/'+tid+'/view')
    expect(page.locator('.detail-layout')).to_be_visible()
    expect(page.locator('#rerununavailable')).to_have_count(0)
    page.mouse.wheel(0,300);page.wait_for_timeout(100)
    assert page.locator('.detail-layout>div:last-child>.panel>.panel-title').first.evaluate("e=>getComputedStyle(e).position")=='static'
    assert page.locator('.detail-head.page-head').evaluate("e=>getComputedStyle(e).position")=='relative'
    page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(100)
    page.screenshot(path=evidence_path('healthy-start-no-recovery-banner.png'))
