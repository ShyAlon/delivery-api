"""Real frontend gesture regressions, no account/model or application mutation."""
from pathlib import Path
import json
import pytest
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/web'

@pytest.fixture
def page():
 with sync_playwright() as p:
  browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1100,'height':850})
  def serve(route):
   path=route.request.url.split('selection.test',1)[-1].split('?',1)[0]
   if path=='/api/tasks':route.fulfill(json={'tasks':[{'task_id':'fixture','title':'Astronomy explorer for curious kids','brief':'A starting description to select and copy from the task card','state':'created','created_at':1,'updated_at':1,'phases':[],'gates':[],'artifact_count':0}]})
   elif path.startswith('/api/'):route.fulfill(json={})
   elif (WEB/path.lstrip('/')).is_file():route.fulfill(path=str(WEB/path.lstrip('/')))
   else:route.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/style.css"><main id="app"></main><div id="modal" class="modal hidden"></div>')
  page.route('http://selection.test/**',serve);page.goto('http://selection.test/')
  page.evaluate('''async()=>{const {createDashboard}=await import('/dashboard.js');const {Dialog}=await import('/shared-dialog.js');window.dash=createDashboard({bindGates(){},bindDialogKeys(){},closeModal(){Dialog.close(document.querySelector('#modal'))}});await dash.dashboard()}''')
  yield page
  browser.close()

def drag(page,start,end):
 page.mouse.move(*start);page.mouse.down();page.mouse.move(*end,steps=15);page.mouse.up()

@pytest.mark.parametrize('selector',['article h2','article .meta'])
def test_card_selection_never_navigates_but_plain_click_does(page,selector):
 box=page.locator(selector).bounding_box();drag(page,(box['x']+5,box['y']+10),(box['x']+190,box['y']+10))
 assert page.evaluate('!!getSelection().toString()')
 assert page.url=='http://selection.test/'
 page.evaluate('getSelection().removeAllRanges()');page.locator(selector).click()
 assert page.url=='http://selection.test/tasks/fixture/view'

@pytest.mark.parametrize('field',['input','textarea'])
def test_new_task_selection_across_backdrop_does_not_dismiss(page,field):
 page.locator('#new').click();input=page.locator('#newform '+field);input.fill('Astronomy explorer for curious kids')
 box=input.bounding_box();drag(page,(box['x']+180,box['y']+20),(8,box['y']+20))
 expect(page.locator('#newform')).to_be_visible();assert input.input_value()=='Astronomy explorer for curious kids'
 page.mouse.click(8,30);expect(page.locator('#newform')).to_have_count(0)


def test_keyboard_and_nested_button_still_work_and_no_field_values_logged(page):
 page.locator('#new').click();page.locator('#newform input').fill('PRIVATE TITLE NEVER LOG')
 page.keyboard.press('Escape');expect(page.locator('#newform')).to_have_count(0)
 page.locator('button.rename').click();expect(page.locator('#renameform')).to_be_visible()
 assert page.url=='http://selection.test/'
 report=page.evaluate("async()=>JSON.stringify((await import('/client-core.js')).browserDiagnostics())")
 assert 'PRIVATE TITLE NEVER LOG' not in report


def test_shared_link_guard_blocks_selection_not_keyboard(page):
 page.evaluate('''async()=>{document.querySelector('#app').innerHTML='<a href="/about">Long selectable navigation link</a>';const router=await import('/spa-router.js');router.defineRoutes([{path:'/',mount(){}},{path:'/about',mount(){}}],()=>{});router.startRouter();document.querySelector('#app').innerHTML='<a href="/about">Long selectable navigation link</a>';}''')
 box=page.locator('a').bounding_box();drag(page,(box['x']+2,box['y']+8),(box['x']+110,box['y']+8))
 assert page.url=='http://selection.test/'
 page.locator('a').focus();page.keyboard.press('Enter');assert page.url=='http://selection.test/about'


def test_gate_sibling_preserves_selection_and_keyboard_activation(page):
 page.evaluate('''async()=>{const {createGate}=await import('/gate.js');document.querySelector('#app').innerHTML='<span class="gate" data-task="fixture" data-gate="intent" tabindex="0">Review selected intent evidence</span>';createGate({}).bindGates([{task_id:'fixture',gates:[{gate_key:'intent',label:'Intent'}]}])}''')
 box=page.locator('.gate').bounding_box();drag(page,(box['x']+1,box['y']+8),(box['x']+100,box['y']+8))
 assert page.url=='http://selection.test/'
 page.locator('.gate').focus();page.keyboard.press('Enter');page.wait_for_url('**/tasks/fixture/gates/intent')


def test_table_header_selection_does_not_reorder(page):
 page.evaluate('''async()=>{const {Table}=await import('/table-component.js');const host=document.querySelector('#app');Table.interactive(host,'Records','',[{key:'name',label:'Selectable record name'}],'Empty').set([{name:'Zebra'},{name:'Apple'}])}''')
 header=page.locator('th').first
 box=header.bounding_box();drag(page,(box['x']+12,box['y']+18),(box['x']+120,box['y']+18))
 assert page.evaluate('!!getSelection().toString()')
 assert page.locator('tbody tr').first.inner_text()=='Zebra'
 page.evaluate('getSelection().removeAllRanges()');header.click()
 assert page.locator('tbody tr').first.inner_text()=='Apple'


def test_history_caller_trace_has_no_state_or_query_values(page):
 page.evaluate("history.replaceState({private:'PRIVATE STATE'},'', '/?private=PRIVATE QUERY')")
 report=page.evaluate("async()=>JSON.stringify((await import('/client-core.js')).browserDiagnostics())")
 assert 'history.replaceState' in report
 assert 'PRIVATE STATE' not in report and 'PRIVATE QUERY' not in report.split('records')[1]
