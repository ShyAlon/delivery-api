"""Shared task view presents the safe failure, not the virtual cleanup code."""
from delivery_api.tenancy import tenant_context
from playwright.sync_api import expect


def test_failed_structure_is_readable_on_shared_task_screen(pg_browser):
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):
        store=workspace.store
        tid=store.create_task('Architecture contract failure')['task_id']
        message='Error: The generated output did not satisfy the required structure. The final rejected item details are not available in this view. Review the task diagnostics before retrying; retrying may cost credits.'
        store.update_phase(tid,'architecture','failed',detail=message)
        store.add_event(tid,'error',message,phase_key='architecture',payload={'exit_code':1})
    page.goto(base+f'/tasks/{tid}/view',wait_until='networkidle')
    expect(page.get_by_text('The generated output did not satisfy the required structure.',exact=False).first).to_be_visible()
    assert 'exit code -9' not in page.locator('body').inner_text()
    page.screenshot(path='/tmp/child-failure-task.png',full_page=True)
