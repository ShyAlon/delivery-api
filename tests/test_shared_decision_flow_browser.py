import pytest
from delivery_api.tenancy import tenant_context

@pytest.mark.parametrize('width,height',[(1475,780),(390,844)])
def test_gate_top_actions_and_fields_visible_and_status_uncovered(pg_browser,width,height):
 from playwright.sync_api import expect
 page,base,w=pg_browser
 with tenant_context(w.scope):
  tid=w.store.create_task('Compact gate')['task_id'];w.store.wait_at_gate(tid,'needs')
 page.set_viewport_size({'width':width,'height':height});page.goto(base+'/tasks/'+tid+'/gates/gate-needs')
 expect(page.locator('.shared-action-strip')).to_be_visible()
 approve=page.locator('[data-action=approve]');box=approve.bounding_box();assert box['y']>=0 and box['y']+box['height']<=height
 expect(page.locator('[name=edited_text]')).to_be_visible();expect(page.locator('[name=steer]')).to_be_visible();expect(page.locator('[name=comment]')).to_be_visible()
 expect(page.locator('.shared-decision-summary')).to_have_count(0)
 assert page.locator('header').evaluate('e=>getComputedStyle(e).position')=='relative'
 assert page.locator('.gate-controls').evaluate('e=>getComputedStyle(e).overflowY')=='visible'
 expect(page.locator('[data-action=edit]')).to_be_in_viewport();expect(page.locator('[data-action=redo]')).to_be_in_viewport()
 page.locator('[name=edited_text]').fill('Preserved draft')
 assert page.locator('[name=edited_text]').input_value()=='Preserved draft'
 page.evaluate("document.querySelector('#gate-decision-status').textContent='Saving decision and starting the next step';window.scrollTo(0,0)")
 status=page.locator('#gate-decision-status').bounding_box();assert status['y']>=0 and status['y']+status['height']<=height
 page.screenshot(path=f'/tmp/shared-gate-{width}.png',full_page=False)

@pytest.mark.parametrize('width,height',[(1475,780),(1280,720),(390,844),(640,480)])
def test_ready_gate_keyboard_pending_failure_and_reserved_space(pg_browser,width,height):
 from playwright.sync_api import expect
 from test_decision_pages_browser import _stack_review
 from urllib.parse import urlparse
 p,base,w=pg_browser;data=_stack_review();data['review']['stack_decisions']['slots']=[];held=[];posts=[]
 data['review']['documents'][0]['view']['content']='{"components":'+str(list(range(200)))+'}'
 def route(r):
  path=urlparse(r.request.url).path
  if path.endswith('/decide'):posts.append(r.request.post_data_json);held.append(r);return
  if path.endswith('/review'):r.fulfill(json=data);return
  if path.endswith('/cost'):r.fulfill(json={});return
  if path.endswith('/stream'):r.fulfill(content_type='text/event-stream',body='');return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.set_viewport_size({'width':width,'height':height})
 p.goto(base+'/tasks/t1/gates/gate-architecture')
 button=p.locator('[data-action=approve]');expect(button).to_be_enabled()
 expect(p.locator('.gate-status')).to_have_text('Your decision is needed')
 expect(p.locator('.gate-status')).to_have_class(__import__('re').compile(r'\binfo\b'))
 expect(p.locator('.decision-other-actions')).to_have_count(0)
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 strip=p.locator('.shared-action-strip')
 expect(button).to_be_in_viewport();assert strip.evaluate('e=>getComputedStyle(e).position')=='fixed'
 p.evaluate('window.scrollTo(0,document.body.scrollHeight)')
 assert p.locator('body').evaluate('e=>parseFloat(getComputedStyle(e).paddingTop)')>=strip.bounding_box()['height']
 button.focus();p.keyboard.press('Enter')
 status=p.locator('#gate-decision-status');expect(status).to_contain_text('Saving decision and starting the next step')
 expect(button).to_be_disabled();expect(status).to_be_in_viewport()
 assert len(posts)==1 and posts[0]['action']=='approve' and posts[0]['selected_option']=='Proceed'
 assert posts[0]['edited_text']==posts[0]['steer']==posts[0]['comment']==''
 rect=status.bounding_box();assert rect and p.evaluate('([x,y])=>document.elementFromPoint(x,y)?.closest("#gate-decision-status")!==null',[rect['x']+5,rect['y']+5])
 p.screenshot(path=f'/tmp/shared-ready-pending-{width}.png')
 held.pop().fulfill(status=503,json={'detail':'Fixture failure: nothing was submitted'})
 expect(status).to_contain_text('Fixture failure');expect(button).to_be_enabled()
 expect(status.locator('.notice')).not_to_have_class(__import__('re').compile(r'\binfo\b'))
 p.screenshot(path=f'/tmp/shared-ready-error-{width}.png')
 expect(p.locator('[data-action=edit]')).to_be_in_viewport()
 expect(p.locator('[name=edited_text]')).to_be_visible()
 p.locator('[name=edited_text]').fill('Reviewed wording')
 # Browser history restores a current deep link, with one strip, not a second clone.
 p.goto(base+'/about');p.go_back();expect(p.locator('.shared-action-strip')).to_have_count(1)
 expect(button).to_be_enabled()
 p.evaluate('window.scrollTo(0,0)');p.screenshot(path=f'/tmp/shared-ready-{width}.png')
 data['actionable']=False;data['historical']=True
 p.reload();expect(button).to_be_disabled()
 expect(p.locator('.shared-decision-summary')).to_have_count(0)
 assert strip.evaluate('e=>getComputedStyle(e).position')=='static'


def test_task_current_decision_inherits_visible_action_strip(pg_browser):
 from playwright.sync_api import expect
 p,base,w=pg_browser
 with tenant_context(w.scope):
  tid=w.store.create_task('Current decision')['task_id'];w.store.wait_at_gate(tid,'needs')
 for width in (1475,390):
  p.set_viewport_size({'width':width,'height':844});p.goto(base+'/tasks/'+tid+'/view')
  expect(p.locator('.shared-action-strip .decision-launch')).to_be_visible()
  expect(p.locator('.shared-action-strip .decision-launch')).to_be_in_viewport()
  expect(p.locator('.shared-decision-summary')).to_have_count(0)
  p.screenshot(path=f'/tmp/shared-task-current-{width}.png')


def test_ops_inherits_document_scroll_and_static_shell(pg_browser):
 from playwright.sync_api import expect
 from test_spa_shell import ui as admin_ui
 # Use the same signed local operator fixture as the existing shell suite.
 p,base,w=pg_browser
 admin_ui.__wrapped__(pg_browser)
 for width,height in ((1475,780),(1280,720),(390,844)):
  p.set_viewport_size({'width':width,'height':height});p.goto(base+'/ops#health')
  expect(p.locator('#panels')).to_be_attached()
  for selector in ('header','#panels','.ui-table-scroll'):
   assert p.locator(selector).first.evaluate('e=>getComputedStyle(e).position')!='sticky'
  for node in p.locator('.ui-table-scroll').all():
   assert node.evaluate('e=>getComputedStyle(e).maxHeight')=='none'
   assert node.evaluate('e=>e.scrollHeight<=e.clientHeight+2')
  p.evaluate('window.scrollTo(0,0)');p.keyboard.press('PageDown');p.wait_for_timeout(150)
  assert p.evaluate('scrollY>0')
  p.screenshot(path=f'/tmp/shared-ops-{width}.png')
