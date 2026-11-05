from test_ui_playwright import pg_ui,pg_page,pg_ids
from playwright.sync_api import expect


def _operator_cookie(page,ui,workspace):
 from delivery_api import config
 from pg_workspace import KEY
 import jwt
 # Preserve real signature/session validation;change only the test actor email.
 claims=jwt.decode(workspace.token,options={'verify_signature':False})
 claims['email']=config.OPERATOR_EMAIL
 page.context.add_cookies([dict(name='requi_access',value=jwt.encode(claims,KEY,algorithm='ES256'),url=ui)])
 return config.OPERATOR_EMAIL


def test_operator_everything_zip_has_manifest_and_unavailable_sections(pg_page,pg_ui,pg_ids,pg_browser,tmp_path):
 page,ui=pg_page,pg_ui
 tid=pg_ids["live"]
 actor=_operator_cookie(page,ui,pg_browser[2])
 page.route('**/api/debug/collection*',lambda r:r.fulfill(json={'sections':[{'source':'durable-diagnostics','availability':'available','data':{'text':'ERROR task=live synthetic diagnostic'}},{'source':'fixture-excluded','availability':'policy_excluded'}]}))
 page.route('**/api/debug/log-durability',lambda r:r.fulfill(json={'degraded':False,'sources':[]}))
 page.goto(ui+'/tasks/'+tid+'/view')
 page.get_by_role('button',name='Account menu for '+actor).click()
 page.get_by_role('menuitem',name='Debug',exact=True).click()
 expect(page.locator('.debug-dialog')).to_be_visible()
 with page.expect_download() as download:
  page.locator('#downloadeverything').click()
 download.value.save_as(str(tmp_path/'debug-package-browser.zip'))
 expect(page.locator('#debugstatus')).to_contain_text('Downloaded')
 page.screenshot(path=str(tmp_path/'debug-package-collection.png'),full_page=False)
 import zipfile
 with zipfile.ZipFile(str(tmp_path/'debug-package-browser.zip')) as z:
  content='\n'.join(z.read(n).decode() for n in z.namelist())
  assert 'Evidence manifest and DB projections' in content
  assert 'durable-diagnostics' in content and 'synthetic diagnostic' in content
  assert 'policy_excluded' in content
  assert 'docker logs' not in content


def test_operator_can_read_durable_evidence_in_dialog_without_script(pg_page,pg_ui,pg_ids,pg_browser,tmp_path):
 page,ui=pg_page,pg_ui
 tid=pg_ids["live"]
 actor=_operator_cookie(page,ui,pg_browser[2])
 page.route('**/api/debug/evidence*',lambda r:r.fulfill(json={'available':True,'availability':'available','text':'WARNING synthetic restart-readable evidence','count':1,'task_id':tid,'truncated':True,'unreadable':[],'storage':'bounded files'}))
 page.goto(ui+'/tasks/'+tid+'/view')
 page.get_by_role('button',name='Account menu for '+actor).click()
 page.get_by_role('menuitem',name='Debug',exact=True).click()
 page.get_by_text('Durable warnings and errors (operator only)',exact=True).click()
 expect(page.locator('#durableevidencehost')).to_contain_text('synthetic restart-readable evidence')
 expect(page.locator('#durableevidencehost')).to_contain_text('truncated')
 page.locator('#durableevidencehost').scroll_into_view_if_needed()
 page.screenshot(path=str(tmp_path/'durable-evidence-dialog.png'),full_page=False)


def test_customer_route_rejects_operator_text_and_links_even_from_stale_api(pg_page,pg_ui,pg_ids,tmp_path):
 page=pg_page
 page.route('**/api/model-routes',lambda r:r.fulfill(json={'managed':True,'routes':[{'id':p,'available':False,'managed':True,'error':'Execution is closed: Choose one reviewed '+p+' model in Back office > Model pins; no implicit fallback','repair':{'kind':'model-pins','label':'Open Model pins in Back office','url':'http://127.0.0.1:8031/#model-pins'}} for p in ('deepseek','ollama')]}))
 page.goto(pg_ui+'/tasks/'+pg_ids['empty']+'/view')
 expect(page.locator('.route-unavailable').first).to_contain_text('DeepSeek is temporarily unavailable')
 expect(page.locator('.route-unavailable').last).to_contain_text('Local interviews are temporarily unavailable')
 assert not any(term in page.locator('body').inner_text().lower() for term in ('back office','model pins','money gateway'))
 expect(page.get_by_role('button',name='Start interview',exact=True)).to_be_disabled()
 assert page.locator('a[href*="8031"],.model-pins-repair').count()==0
 page.screenshot(path=str(tmp_path/'customer-route-boundary.png'),full_page=True)


def test_shared_loading_is_animated_and_respects_reduced_motion(pg_page,pg_ui,tmp_path):
 page=pg_page
 page.emulate_media(reduced_motion='no-preference')
 page.goto(pg_ui+'/')
 expect(page.get_by_role('button',name='+ New task',exact=True)).to_be_visible()
 page.evaluate('document.querySelector("#app").innerHTML=\'<div class="loading route-loading" role="status">Loading…</div>\'')
 status=page.get_by_role('status').filter(has_text='Loading…')
 expect(status).to_be_visible()
 assert status.evaluate('e=>getComputedStyle(e,"::before").animationName')=='workspace-loading-spin'
 page.screenshot(path=str(tmp_path/'customer-loading-motion.png'))
 page.emulate_media(reduced_motion='reduce')
 assert status.evaluate('e=>getComputedStyle(e,"::before").animationName')=='none'


def test_empty_model_pins_disable_routes_and_show_fixed_operator_repair(pg_page,pg_ui,pg_ids,pg_browser,tmp_path):
 page=pg_page
 _operator_cookie(page,pg_ui,pg_browser[2])
 page.route('**/api/model-routes',lambda r:r.fulfill(json={'managed':True,'default_route':None,'routes':[{'id':p,'available':False,'managed':True,'reason':'no_approved_model_pin','operator_repair':True,'error':'private arbitrary text must never render'} for p in ('deepseek','ollama')]}))
 page.goto(pg_ui+'/tasks/'+pg_ids['empty']+'/view')
 for name in ('DeepSeek','Ollama'):
  expect(page.get_by_role('button',name=name,exact=True)).to_be_disabled()
 expect(page.get_by_role('button',name='Start interview',exact=True)).to_be_disabled()
 expect(page.locator('.route-unavailable').first).to_contain_text('No approved model pin. Approve one in Back office > Model pins.')
 assert 'private arbitrary text' not in page.locator('body').inner_text()
 assert page.locator('[data-start-route][aria-pressed="true"]').count()==0
 expect(page.locator('.not-started')).to_contain_text('Your task is saved.')
 page.screenshot(path='/downloads/empty-pin-readiness-desktop.png',full_page=True)
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.screenshot(path='/downloads/empty-pin-readiness-mobile.png',full_page=True)
