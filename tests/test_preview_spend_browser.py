"""Paid route selectable without authorizing a call; native confirmation preserves task."""
import json
from delivery_api.tenancy import tenant_context


def test_paid_route_requires_explicit_ceiling_dialog(pg_browser):
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):tid=workspace.store.create_task('Paid confirmation preview','A salon booking app')['task_id']
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[
        {'id':'deepseek','available':True,'managed':True,'confirmation_required':True,'model':'deepseek:deepseek-flash','model_map':{'probing':['deepseek','deepseek-flash']}},
        {'id':'ollama','available':True,'managed':True,'model':'ollama:phi4','model_map':{'probing':['ollama','phi4']}}],
        'default_route':{'route':'ollama'}}))
    page.goto(base+f'/tasks/{tid}/view');page.locator('[data-start-route=deepseek]').click()
    assert 'Confirmation required' in page.locator('[data-start-route=deepseek]').inner_text()
    seen=[]
    def cancel(dialog):seen.append(dialog.message);dialog.dismiss()
    page.on('dialog',cancel)
    page.locator('#startengine').click()
    page.locator('#startstatus').get_by_text('Paid start cancelled.',exact=False).wait_for()
    assert 'Maximum provider expense in USD' in seen[0]
    assert page.locator('#startengine').is_enabled()
    with tenant_context(workspace.scope):assert workspace.store.task(tid)['state']=='not_started'
    page.screenshot(path='/downloads/paid-confirmation-required.png',full_page=True)
