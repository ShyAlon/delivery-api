from pathlib import Path
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/web'

def test_query_only_navigation_and_history_keep_shell_and_mount_current_route():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(args=['--no-sandbox']);p=browser.new_page()
  def route(r):
   name=r.request.url.split('/')[-1].split('?')[0]
   if name.endswith('.js'):r.fulfill(content_type='text/javascript',body=(WEB/name).read_text());return
   r.fulfill(content_type='text/html',body='<header id="shell">Persistent</header><main id="app"></main>')
  p.route('http://router.test/**',route);p.goto('http://router.test/review?revision=old')
  p.evaluate('''async()=>{const r=await import('/spa-router.js');window.router=r;window.mounts=0;window.shell=document.querySelector('header');r.defineRoutes([{path:'/review',mount:()=>{mounts++;document.querySelector('#app').textContent=location.search||'current'}}],()=>{});r.startRouter()}''')
  expect(p.locator('#app')).to_have_text('?revision=old')
  p.evaluate("router.navigate('/review')")
  expect(p.locator('#app')).to_have_text('current');assert p.evaluate('mounts')==2
  p.evaluate("router.navigate('/review?tab=comments&draft=saved')")
  expect(p.locator('#app')).to_have_text('?tab=comments&draft=saved');assert p.evaluate('mounts')==3
  p.evaluate("router.navigate('/review?tab=comments&draft=saved')");assert p.evaluate('mounts')==3
  p.go_back();expect(p.locator('#app')).to_have_text('current')
  p.go_back();expect(p.locator('#app')).to_have_text('?revision=old')
  p.go_forward();expect(p.locator('#app')).to_have_text('current')
  assert p.evaluate('shell===document.querySelector("header")')
  browser.close()


def test_historical_findings_offer_current_review_with_same_path(pg_browser):
 from copy import deepcopy
 from urllib.parse import urlparse,parse_qs
 from test_decision_pages_browser import review
 p,base,w=pg_browser
 current=review();current['kind']='semantic_review';current['options']=[];current['revision']='engine:new'
 current['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'FD-1','reason':'Needs a decision','kind':'semantic','original':'Fast app'}]}}}}
 old=deepcopy(current);old['revision']='engine:old'
 def route(r):
  q=parse_qs(urlparse(r.request.url).query);historical=q.get('revision',[''])[0]=='engine:old'
  r.fulfill(json={'task_id':'concept','title':'Fixture','review':old if historical else current,'current':current,'actionable':not historical,'comments':[]})
 p.route('**/api/tasks/concept**',route)
 p.goto(base+'/tasks/concept/decisions/wait-concept?revision=engine%3Aold')
 expect(p.locator('input[name="action-0"][value=accept]')).to_be_disabled()
 p.get_by_role('link',name='Open current review',exact=True).click()
 expect(p.locator('input[name="action-0"][value=accept]')).to_be_enabled()
 expect(p).to_have_url(__import__('re').compile('.*revision=engine%3Anew.*'))
