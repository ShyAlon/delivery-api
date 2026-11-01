"""Customer start is credits-only, never provider expense approval."""
from delivery_api.tenancy import tenant_context

def test_paid_customer_start_has_no_native_dialog_or_provider_cost(pg_browser):
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):tid=workspace.store.create_task('Credits-only preview','Salon app')['task_id']
    page.route('**/api/model-routes',lambda route:route.fulfill(json={'routes':[{'id':'deepseek','available':True,'managed':True,'model':'deepseek:deepseek-flash'}],'default_route':{'route':'deepseek','model':'deepseek-flash'}}))
    page.route('**/api/tasks/*/start',lambda route:route.fulfill(status=503,json={'detail':'This model is temporarily unavailable.'}))
    dialogs=[];page.on('dialog',lambda d:(dialogs.append(d.message),d.dismiss()))
    page.goto(base+f'/tasks/{tid}/view');page.locator('#startengine').click()
    page.locator('#startstatus').get_by_text('This model is temporarily unavailable.',exact=False).wait_for()
    assert not dialogs
    text=page.locator('body').inner_text()
    assert '$' not in page.locator('#sessioncost').inner_text()
    assert 'USD' not in text and 'provider expense' not in text and 'Confirmation required' not in text
    assert 'Billed to your credits' in text
    page.screenshot(path='/downloads/customer-credits-only.png',full_page=True)
