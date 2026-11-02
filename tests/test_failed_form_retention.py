"""Real browser with disposable PG; failed requests never replace form DOM."""
from test_artifacts import evidence_path
from pathlib import Path
from delivery_api.tenancy import tenant_context


def test_create_and_rename_failure_preserve_fields(pg_browser):
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):workspace.store.create_task('Existing name','Saved')
    page.goto(base+'/');page.locator('#new').click()
    page.locator('#newform [name=title]').fill('Existing name')
    page.locator('#newform [name=brief]').fill('Keep this exact multiline brief\nsecond line')
    page.locator('#newform button.primary').click()
    page.locator('#newform [data-submit-error]').wait_for()
    assert page.locator('#newform [name=title]').input_value()=='Existing name'
    assert page.locator('#newform [name=brief]').input_value()=='Keep this exact multiline brief\nsecond line'
    assert page.locator('#newform [name=title]').get_attribute('aria-invalid')=='true'
    assert page.locator('#newform button.primary').is_enabled()
    Path(evidence_path('form-retention')).mkdir(exist_ok=True)
    page.screenshot(path=evidence_path('form-retention/create-error-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=evidence_path('form-retention/create-error-mobile.png'),full_page=True)
    page.locator('#newform [name=title]').fill('Corrected name')
    page.locator('#newform button.primary').click()
    page.wait_for_url('**/tasks/*/view')
    page.locator('#renametask').click()
    page.locator('#renameform [name=title]').fill('Existing name')
    page.locator('#renameform button.primary').click()
    page.locator('#renameform [data-submit-error]').wait_for()
    assert page.locator('#renameform [name=title]').input_value()=='Existing name'
    assert 'already exists' in page.locator('#renameform [data-submit-error]').inner_text()


def test_stale_decision_keeps_readonly_copy_instead_of_timed_discard():
    text=(Path(__file__).parents[1]/'src/delivery_api/web/dialogs.js').read_text()
    assert 'Your input is kept here for copying.' in text
    assert "setTimeout(()=>{modal.classList.add('hidden');modal.innerHTML='';detail(id)},700)" not in text
