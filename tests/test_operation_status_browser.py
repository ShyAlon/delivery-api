"""One shared status state machine, deterministic pending promises and real DOM."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from test_artifacts import evidence_path
WEB=Path(__file__).parents[1]/'src/delivery_api/web'

def test_shared_status_concurrency_action_error_and_no_content_leak():
 with sync_playwright() as p:
  browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':844})
  def route(r):
   if r.request.url.endswith('.js'):r.fulfill(content_type='text/javascript',body=(WEB/r.request.url.split('/')[-1]).read_text())
   else:r.fulfill(content_type='text/html',body='<main><h1>Operation status fixture</h1><button>Delete task</button></main><footer></footer>')
  page.route('http://status.test/**',route);page.goto('http://status.test')
  page.evaluate("""async()=>{const s=await import('/operation-status.js');window.s=s;s.mountOperationStatus(document.querySelector('footer'));window.a=s.beginOperation('Deleting task 5afd',{kind:'action',success:'Task hidden; accounting retained',failure:'Delete unverified; refresh before retrying'});window.b=s.beginOperation('Loading jobs')}""")
  view=page.locator('[data-operation-status]');expect(view).to_contain_text('Deleting task 5afd');expect(view).to_have_attribute('aria-busy','true')
  page.screenshot(path=evidence_path('operation-status-busy-phone.png'))
  page.evaluate("b('success');a('success')");expect(view).to_contain_text('Task hidden; accounting retained');expect(view).to_have_attribute('data-state','success')
  page.evaluate("s.beginOperation('Loading build')('success')");expect(view).to_contain_text('Task hidden; accounting retained')
  page.evaluate("window.a=s.beginOperation('Deleting task 5afd',{kind:'action',failure:'Delete unverified; refresh before retrying'});s.beginOperation('Delete request')('error');a('success')")
  expect(view).to_have_attribute('data-state','error');expect(view).to_contain_text('Delete unverified')
  page.screenshot(path=evidence_path('operation-status-error-phone.png'))
  assert page.evaluate("s.snapshot().pending")==0
  page.evaluate("const old=s.beginOperation('Old read');const action=s.beginOperation('New action',{kind:'action',success:'New action verified'});old('error');action('success')")
  expect(view).to_have_attribute('data-state','success');expect(view).to_contain_text('New action verified')
  page.evaluate("s.beginOperation('New read')('error')");expect(view).to_have_attribute('data-state','error')
  assert page.evaluate("s.requestDescription('/api/example',{method:'POST',body:JSON.stringify({secret:'not for status'})})")== 'Sending action to server'
  browser.close()


from test_backoffice_shared_ui import ui


def test_backoffice_real_delete_pending_success_error_and_read_status(ui):
 url,calls=ui
 with sync_playwright() as p:
  browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':950},extra_http_headers={'Authorization':'Bearer fake'})
  page.goto(url);page.locator('#active-jobs tbody tr').wait_for()
  page.wait_for_function("document.querySelector('[data-operation-status]').getAttribute('aria-busy')==='false'")
  pending=[]
  page.route('**/api/operations/worker-jobs/job-one/stop',lambda route:pending.append(route))
  page.get_by_role('button',name='Job actions',exact=True).click();page.get_by_role('menuitem',name='Delete',exact=True).click();page.get_by_role('button',name='Stop and delete',exact=True).click()
  status=page.locator('.operator-bottom-bar [data-operation-status]');expect(status).to_contain_text('Deleting task task-one');expect(status).to_have_attribute('aria-busy','true')
  expect(page.locator('dialog [data-operation-status]')).to_contain_text('Deleting task task-one')
  page.screenshot(path=evidence_path('backoffice-status-dialog-desktop.png'))
  page.get_by_role('button',name='Cancel',exact=True).click()
  page.screenshot(path=evidence_path('backoffice-status-deleting-desktop.png'),full_page=False)
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path=evidence_path('backoffice-status-deleting-phone.png'),full_page=False)
  assert status.bounding_box()['y']+status.bounding_box()['height']<=844
  assert len(pending)==1
  pending.pop().fulfill(json={'hidden':True,'stop_requested':True,'accounting_retained':True})
  expect(status).to_have_attribute('data-state','success');expect(status).to_contain_text('Completed: Deleting task task-one')
  page.screenshot(path=evidence_path('backoffice-status-success-phone.png'))
  page.get_by_role('button',name='Job actions',exact=True).click();page.get_by_role('menuitem',name='Delete',exact=True).click();page.get_by_role('button',name='Stop and delete',exact=True).click()
  page.wait_for_function("document.querySelector('[data-operation-status]').getAttribute('aria-busy')==='true'")
  page.wait_for_timeout(50)
  pending.pop().fulfill(status=409,json={'detail':'Outcome unverified. Refresh jobs.'})
  expect(status).to_have_attribute('data-state','error');expect(status).to_contain_text('Failed')
  page.get_by_role('button',name='Cancel',exact=True).click()
  page.screenshot(path=evidence_path('backoffice-status-error-phone.png'),full_page=False)
  reads=[];page.route('**/api/operations/active-jobs?*',lambda route:reads.append(route))
  page.locator('#active-jobs-refresh').click();expect(status).to_contain_text('Loading worker jobs');expect(status).to_have_attribute('aria-busy','true')
  page.wait_for_timeout(50);reads.pop().abort();expect(status).to_have_attribute('data-state','error')
  browser.close()
