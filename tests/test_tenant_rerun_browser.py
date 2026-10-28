from test_ui_playwright import ui,page,_promote_control
from playwright.sync_api import expect


def test_failed_tenant_interview_explains_unavailable_rerun_without_confirm(page,ui):
    live=page.request.get(ui+'/api/tasks/live').json()
    live.update({'state':'failed','active_phase':'intent','simulated':False,
        'input_state':{'waiting':False,'process_live':False},
        'ui_state':{'mode':'error','active_phase':'intent','process_live':False},
        'rerun_control':{'available':False,'reason':'Tenant stage rerun is unavailable: new-generation session/checkpoint recovery is not implemented.'}})
    live['events'].append({'id':9999,'kind':'error','message':'Interview failed','phase_key':'intent','payload':{},'ts':1720004001})
    page.route('**/api/tasks/live',lambda r:r.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.locator('#rerununavailable')).to_contain_text('new-generation')
    expect(page.locator('#rerununavailable')).to_contain_text('Audit')
    page.screenshot(path='/downloads/tenant-rerun-unavailable.png',full_page=True)


import pytest

@pytest.mark.parametrize('phase_key,button',[('needs','retryneeds'),('srs','retryphase')])
def test_tenant_supported_generator_retry_stays_available(page,ui,phase_key,button):
    live=page.request.get(ui+'/api/tasks/live').json()
    found=False
    for phase in live['phases']:
        if phase['phase_key']==phase_key:
            phase['state']='failed';found=True
        else:phase['state']='pending' if found else 'approved'
    live.update({'state':'failed','active_phase':phase_key,'simulated':False,
        'input_state':{'waiting':False,'process_live':False,'engine_process_status':40},
        'ui_state':{'mode':'error','active_phase':phase_key,'process_live':False},
        'rerun_control':{'available':False,'reason':'Tenant stage rerun is unavailable: new-generation session/checkpoint recovery is not implemented.'}})
    page.route('**/api/tasks/live',lambda r:r.fulfill(json=_promote_control(live)))
    page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
    page.goto(ui+'/tasks/live/view')
    expect(page.locator('#'+button)).to_be_visible()
    expect(page.locator('#rerun')).to_have_count(0)
    expect(page.locator('#rerununavailable')).to_contain_text('Use the phase Retry')
