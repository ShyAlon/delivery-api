from copy import deepcopy
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import expect
import pytest

def _semantic_fixture(pg_browser):
 from test_decision_pages_browser import _stack_review
 p,base,w=pg_browser
 old=_stack_review();old['review']['stack_decisions']=None;old['review']['phase_key']='srs'
 task=old['review']['task'];task['gates'][0].update(gate_key='gate-srs',after_phase='srs',label='requirements')
 task['phases']=[{'phase_key':'srs','label':'Requirements'},{'phase_key':'architecture','label':'Architecture'}]
 task['artifacts'][0].update(phase_key='srs',name='srs.json')
 old['review']['documents']=[{'name':'srs.json','view':{'format':'json','content':'{"purpose":"Fixture requirements"}'}}]
 old['review']['semantic_review']={'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'R-1','kind':'semantic','reason':'Review this requirement','path':['functional',0],'original':{'statement':'Fixture requirement'}}],'decisions':[]}
 old['review']['revision']='gate:old';state={'current':deepcopy(old),'posts':[],'hold':False,'held':[],'failure':False}
 def route(r):
  path=urlparse(r.request.url).path
  if path.endswith('/semantic-review'):
   state['posts'].append(r.request.post_data_json)
   if state['hold']:state['held'].append(r);return
   if state['failure']:r.fulfill(status=422,json={'detail':'Fixture: item decision rejected'});return
   state['current']=deepcopy(old);state['current']['review']['revision']='gate:new'
   state['current']['review']['semantic_review']['decisions']=[{'item_id':'R-1','action':'accept'}]
   r.fulfill(json={'status':'reviewed','revision':'a'*64});return
  if path.endswith('/review'):
   pinned=parse_qs(urlparse(r.request.url).query).get('revision',[None])[0]
   out=deepcopy(old if pinned=='gate:old' else state['current'])
   out['historical']=out['review']['revision']!=state['current']['review']['revision'];out['actionable']=not out['historical']
   r.fulfill(json=out);return
  if path=='/api/tasks/t1':r.fulfill(json=task);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path.endswith('/cost'):r.fulfill(json={});return
  if path.endswith('/stream'):r.fulfill(content_type='text/event-stream',body='');return
  r.continue_()
 p.route('**/api/**',route)
 return p,base,w,state,old

@pytest.mark.parametrize('width,height',[(1475,780),(390,844)])
def test_stale_semantic_gate_reopen_current_path(pg_browser,width,height):
 p,base,w,state,old=_semantic_fixture(pg_browser)
 p.set_viewport_size({'width':width,'height':height});p.goto(base+'/tasks/t1/gates/gate-srs')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 jump=p.locator('#decision-checklist [data-jump="#semantic-panel"]');jump.click()
 expect(p.locator('#semantic-panel')).to_be_in_viewport()
 p.locator('#semantic-panel input[value=accept]').check()
 p.locator('[name=comment]').fill('Keep this unsaved note')
 p.locator('#apply-semantic').click()
 expect(p.locator('[name=comment]')).to_have_value('Keep this unsaved note')
 expect(p.locator('[data-action=approve]')).to_be_enabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Ready to continue')
 expect(p.locator('#gate-decision-status')).to_contain_text('Item decisions saved')
 assert 'gate%3Anew' in p.url
 p.wait_for_timeout(5500)
 expect(p.locator('[data-action=approve]')).to_be_enabled()
 # A deliberately opened historical deep link offers a working same-path current link.
 p.goto(base+'/tasks/t1/gates/gate-srs?revision=gate%3Aold')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 p.get_by_role('link',name='Open current gate',exact=True).click()
 expect(p.locator('[data-action=approve]')).to_be_enabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Ready to continue')
 assert len(state['posts'])==1
 p.screenshot(path=f'/tmp/requirements-auto-current-{width}.png',full_page=True)


def test_real_pg_saved_semantic_choices_auto_refresh_authoritative_revision(pg_browser,tmp_path,monkeypatch):
 from delivery_api.tenancy import tenant_context
 from tests.test_gate_reviews import _seed_srs_gate
 from vmodel_contracts.semantic_review import make_review
 import delivery_api.app as module
 p,base,w=pg_browser
 with tenant_context(w.scope):
  store,tid,client,spec,body,semantic=_seed_srs_gate(tmp_path,monkeypatch,w)
  sid=store.task(tid)['engine_session_id']
  flagged=make_review(sid,'srs',body,[{'finding_id':'f1','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Specifies a widget','original':body['functional'][0]}])
  (spec/'srs.semantic-review.json').write_text(flagged.model_dump_json())
 monkeypatch.setattr(module.config,'CLOUD_TENANTS',True)
 # Model discovery/defaults are irrelevant to this no-model semantic write.
 p.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[]}))
 p.route('**/generator-default/*',lambda r:r.fulfill(json={'model':'fixture','source':'Current task default'}))
 posts=[];p.on('request',lambda r:posts.append(r.url) if r.method=='POST' and '/semantic-review' in r.url else None)
 p.goto(base+f'/tasks/{tid}/gates/gate-srs')
 old_url=p.url;expect(p.locator('[data-action=approve]')).to_be_disabled()
 p.locator('#semantic-panel input[value=accept]').check()
 p.locator('#apply-semantic').click()
 expect(p.locator('[data-action=approve]')).to_be_enabled(timeout=10000)
 expect(p.locator('#gate-decision-status')).to_contain_text('Item decisions saved')
 assert p.url!=old_url
 with tenant_context(w.scope):
  import json
  recorded=json.loads(store.read_artifact_bytes(tid,'srs','srs.semantic-review.json'))
  assert recorded['decisions'][0]['item_id']=='R-1' and recorded['decisions'][0]['action']=='accept'
 p.wait_for_timeout(5500);expect(p.locator('[data-action=approve]')).to_be_enabled()
 assert len(posts)==1
 p.screenshot(path='/tmp/requirements-real-pg-auto-current.png',full_page=True)


def test_failed_semantic_save_preserves_choices_and_does_not_refresh(pg_browser):
 p,base,w,state,old=_semantic_fixture(pg_browser);state['failure']=True
 p.goto(base+'/tasks/t1/gates/gate-srs');expect(p.locator('#apply-semantic')).to_be_visible();old_url=p.url
 p.locator('#semantic-panel input[value=accept]').check();p.locator('[name=comment]').fill('Unsaved note')
 p.locator('#apply-semantic').click()
 expect(p.locator('#semantic-status')).to_contain_text('item decision rejected')
 expect(p.locator('#apply-semantic')).to_be_enabled()
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 expect(p.locator('[name=comment]')).to_have_value('Unsaved note');assert p.url==old_url
 expect(p.locator('#semantic-panel input[value=accept]')).to_be_checked()


def test_late_semantic_save_never_navigates_back_over_new_page(pg_browser):
 p,base,w,state,old=_semantic_fixture(pg_browser);state['hold']=True
 p.goto(base+'/tasks/t1/gates/gate-srs')
 p.locator('#semantic-panel input[value=accept]').check();p.locator('#apply-semantic').click()
 expect(p.locator('#apply-semantic')).to_be_disabled()
 p.get_by_role('link',name='About',exact=True).click();expect(p.locator('main')).to_contain_text('Requi')
 assert state['held'];state['held'].pop().fulfill(json={'status':'reviewed','revision':'a'*64})
 p.wait_for_timeout(500)
 assert urlparse(p.url).path=='/about';expect(p.locator('#gateform')).to_have_count(0)


def test_external_evidence_change_keeps_unsaved_text_and_offers_current_gate(pg_browser):
 p,base,w,state,old=_semantic_fixture(pg_browser)
 p.goto(base+'/tasks/t1/gates/gate-srs');p.locator('[name=comment]').fill('Unsaved note')
 state['current']['review']['revision']='gate:external'
 p.wait_for_timeout(5500)
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 expect(p.locator('[name=comment]')).to_have_value('Unsaved note')
 p.get_by_role('link',name='Open current gate',exact=True).click()
 expect(p).to_have_url(__import__('re').compile('.*gate%3Aexternal$'))
 expect(p.locator('#decision-checklist')).not_to_contain_text('evidence changed')
 expect(p.locator('[data-action=approve]')).to_be_disabled() # unresolved flag is not bypassed
