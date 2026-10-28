from test_ui_playwright import ui,page
from playwright.sync_api import expect


def test_operator_everything_zip_has_manifest_and_unavailable_sections(page,ui):
 page.route('**/api/debug/evidence*',lambda r:r.fulfill(json={'available':True,'text':'ERROR task=live synthetic diagnostic','count':1,'task_id':'live','truncated':False}))
 page.route('**/api/debug/log-durability',lambda r:r.fulfill(json={'degraded':False,'sources':[]}))
 page.goto(ui+'/tasks/live/view')
 page.get_by_role('button',name='Account menu for local user').click()
 page.get_by_role('menuitem',name='Debug',exact=True).click()
 expect(page.locator('.debug-dialog')).to_be_visible()
 with page.expect_download() as download:
  page.locator('#downloadeverything').click()
 download.value.save_as('/downloads/debug-package-browser.zip')
 expect(page.locator('#debugstatus')).to_contain_text('Downloaded')
 page.screenshot(path='/downloads/debug-package-collection.png',full_page=False)
 import zipfile
 with zipfile.ZipFile('/downloads/debug-package-browser.zip') as z:
  content='\n'.join(z.read(n).decode() for n in z.namelist())
  assert 'Evidence manifest and DB projections' in content
  assert 'durable-diagnostics' in content and 'synthetic diagnostic' in content
  assert 'policy_excluded' in content
  assert 'docker logs' not in content


def test_operator_can_read_durable_evidence_in_dialog_without_script(page,ui):
 page.route('**/api/debug/evidence*',lambda r:r.fulfill(json={'available':True,'availability':'available','text':'WARNING synthetic restart-readable evidence','count':1,'task_id':'live','truncated':True,'unreadable':[],'storage':'bounded files'}))
 page.goto(ui+'/tasks/live/view')
 page.get_by_role('button',name='Account menu for local user').click()
 page.get_by_role('menuitem',name='Debug',exact=True).click()
 page.get_by_text('Durable warnings and errors (operator only)',exact=True).click()
 expect(page.locator('#durableevidencehost')).to_contain_text('synthetic restart-readable evidence')
 expect(page.locator('#durableevidencehost')).to_contain_text('truncated')
 page.locator('#durableevidencehost').scroll_into_view_if_needed()
 page.screenshot(path='/downloads/durable-evidence-dialog.png',full_page=False)
