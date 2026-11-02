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


def test_closed_route_names_pin_review_and_links_private_backoffice(pg_page,pg_ui,pg_ids,tmp_path):
 page=pg_page
 page.route('**/api/model-routes',lambda r:r.fulfill(json={'managed':True,'routes':[{'id':p,'available':False,'managed':True,'error':'Execution is closed: Choose one reviewed '+p+' model in Back office > Model pins; no implicit fallback','repair':{'kind':'model-pins','label':'Open Model pins in Back office','url':'http://127.0.0.1:8031/#model-pins'}} for p in ('deepseek','ollama')]}))
 page.goto(pg_ui+'/tasks/'+pg_ids['empty']+'/view')
 expect(page.locator('.route-unavailable').first).to_contain_text('Choose one reviewed deepseek')
 expect(page.get_by_role('button',name='Start interview',exact=True)).to_be_disabled()
 expect(page.locator('.model-pins-repair').first).to_have_attribute('href','http://127.0.0.1:8031/#model-pins')
 page.screenshot(path='/downloads/closed-model-pins-repair.png',full_page=True)
