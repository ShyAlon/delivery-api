"""Browser contracts for typed ConOps pages; no model calls or user accounts."""
import json,os,socket,subprocess,sys,time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect

@pytest.fixture(scope='module')
def server(tmp_path_factory):
 root=tmp_path_factory.mktemp('decision-ui')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 env={**os.environ,'DELIVERY_DB_PATH':str(root/'ui.db'),'VMODEL_ARTIFACTS_DIR':str(root/'artifacts'),'PYTHONPATH':str(Path(__file__).parents[1]/'src')}
 p=subprocess.Popen([sys.executable,'-m','uvicorn','delivery_api.app:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 base=f'http://127.0.0.1:{port}'
 for _ in range(60):
  try:__import__('urllib.request',fromlist=['urlopen']).urlopen(base+'/health',timeout=.3);break
  except Exception:time.sleep(.1)
 yield base,root
 p.terminate();p.wait(5)

def review(wait='wait-concept',purpose='conops_concept'):
 evidence={'conops_draft':{'stakeholder_context':[{'stakeholder':'Runner','goals':['Fast habit capture'],'operating_environment':'Phone, often offline'}],'operational_scenarios':[{'name':'Log a habit','operational_flow':['Open the app','Record points'],'success_outcome':'Points sync when online'}],'concept_options':[]}}
 if purpose=='interview_approval':evidence={'intent_model':{'problem_statement':'Runners need simple habit tracking','target_users':['Runner']},'conops':{'selected_concept':'PWA','scenarios':[{'name':'Log a habit offline'}]}}
 options=[{'value':'1','label':'Native app','summary':'Installed client with native APIs','advantages':['Native device access'],'disadvantages':['Store releases']},{'value':'2','label':'PWA','summary':'One installable web product','advantages':['One deployment'],'disadvantages':['Platform limits']}]
 if purpose=='interview_approval':options=[{'value':1,'label':'Proceed'},{'value':0,'label':'Edit'}]
 return {'version':2,'task_id':'concept','phase_key':'conops','owner':'engine','mode':'waiting_input','kind':'choice' if purpose=='conops_concept' else 'approval','wait_id':wait,'revision':'engine:s:3' if purpose=='conops_concept' else 'engine:s:5','precondition':{'session_id':'s','session_sequence':3 if purpose=='conops_concept' else 5},'options':options,'prompt':'Choose which concept should execute your intent.' if purpose=='conops_concept' else 'Review your intent and ConOps.','decision':{'rationale_required':purpose=='conops_concept','context':{'decision_purpose':purpose,'evidence':evidence}}}

@pytest.fixture
def page(server):
 base,root=server
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1365,'height':1000});yield page,base,root;browser.close()

def fake(page,actionable=True):
 state={'current':review(),'drafts':{},'posts':[]}
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  data=r.request.post_data_json if r.request.method=='POST' else {}
  if path=='/api/tasks/concept':r.fulfill(json={'control_snapshot':state['current']});return
  if path.endswith('/answer'):
   state['posts'].append(data);state['current']=review('wait-review','interview_approval');r.fulfill(json={'status':'accepted'});return
  if path.endswith('/drafts'):
   state['drafts']['saved-one']=data;r.fulfill(json={'draft_id':'saved-one'});return
  if path.endswith('/comments'):state['posts'].append({'annotation':data});r.fulfill(json={'comment_id':1});return
  q=__import__('urllib.parse',fromlist=['parse_qs','urlparse']).parse_qs(__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).query)
  current=state['current'];historical=path.endswith('/wait-concept') and current['wait_id']!='wait-concept';shown=review() if historical else current
  r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':shown,'current':current,'actionable':actionable and not historical,'draft':state['drafts'].get(q.get('draft',[''])[0])})
 page.route('**/api/tasks/concept**',route)
 return state

def test_choose_save_reload_continue_and_historical_back(page):
 p,base,root=page;state=fake(p)
 p.goto(base+'/tasks/concept/decisions/wait-concept');expect(p.locator('h1')).to_have_text('Choose the operating concept')
 assert p.locator('input[name=choice]:checked').count()==0;assert p.locator('[role=dialog]').count()==0
 p.locator('input[value="2"]').check();p.locator('textarea[name=rationale]').fill('One deployment is enough');p.locator('textarea[name=comment]').fill('Check offline behavior')
 expect(p.locator('#draft-status')).to_contain_text('Unsaved')
 p.get_by_role('button',name='Save draft',exact=True).click();expect(p.locator('#draft-status')).to_contain_text('Draft saved')
 link=p.url;assert 'draft=saved-one' in link and 'item=2' in link
 p.reload();expect(p.locator('textarea[name=rationale]')).to_have_value('One deployment is enough');expect(p.locator('input[value="2"]')).to_be_checked()
 p.screenshot(path=str(root/'concept-desktop.png'),full_page=True)
 p.get_by_role('button',name='Choose and continue').click();expect(p.locator('h1')).to_have_text('Review before continuing');assert 'wait-review' in p.url; p.screenshot(path=str(root/'final-review.png'),full_page=True)
 assert state['posts'][0]['answer']=='2 - One deployment is enough';assert state['posts'][0]['comment']=='Check offline behavior'
 p.goto(link);expect(p.locator('.notice').first).to_contain_text('Historical review');expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled()
 assert len(state['posts'])==1

def test_mobile_comment_only_and_no_overflow(page):
 p,base,root=page;state=fake(p);p.set_viewport_size({'width':390,'height':844})
 p.goto(base+'/tasks/concept/decisions/wait-concept');p.locator('textarea[name=comment]').fill('Keep context visible');p.get_by_role('button',name='Save comment only').click()
 expect(p.locator('#review-status')).to_contain_text('No generation or decision changed')
 assert state['current']['wait_id']=='wait-concept';assert 'annotation' in state['posts'][0]
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 p.evaluate('window.scrollTo(0,0)');p.screenshot(path=str(root/'concept-mobile.png'),full_page=True)

def test_stale_live_precondition_disables_submission(page):
 p,base,root=page;state=fake(p);p.goto(base+'/tasks/concept/decisions/wait-concept');p.locator('input[value="1"]').check();p.locator('textarea[name=rationale]').fill('Native APIs needed');state['current']=review('other-wait')
 p.get_by_role('button',name='Choose and continue').click();expect(p.locator('#review-status')).to_contain_text('review changed')
 assert not state['posts'];expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled()

def test_avatar_debug_dialog_download_and_default_is_not_debug(page):
 p,base,root=page;fake(p)
 p.route('**/api/tasks/concept/execution?debug=1',lambda r:r.fulfill(json={'session_id':'s','calls':[],'usage':[],'raw':[]}))
 downloads=[]
 p.route('**/api/tasks/concept/debug.zip',lambda r:(downloads.append(r.request.post_data_json),r.fulfill(status=200,content_type='application/zip',body=b'zip fixture')))
 p.goto(base+'/tasks/concept/decisions/wait-concept');p.locator('#accountbutton').click();p.get_by_role('menuitem',name='Debug',exact=True).click()
 expect(p.locator('.debug-dialog h2')).to_have_text('Debug');expect(p.locator('.debug-dialog')).to_contain_text('Browser capture: this tab since')
 p.get_by_role('button',name='Download debug ZIP').click();expect(p.locator('#debugstatus')).to_contain_text('Download prepared')
 assert downloads and downloads[0]['started_at'];assert downloads[0]['records']
 assert p.locator('#debugtoggle').count()==0 and p.locator('.execution-data').count()==0
 p.screenshot(path=str(root/'avatar-debug.png'),full_page=True)

def test_accepted_decision_followup_failure_never_reenables_submit(page):
 p,base,root=page;state=fake(p);reads={'n':0}
 def task_read(route):
  reads['n']+=1
  if reads['n']>1:route.fulfill(status=503,json={'detail':{'error':'Next snapshot unavailable'}})
  else:route.fulfill(json={'control_snapshot':state['current']})
 p.route('**/api/tasks/concept',task_read)
 p.goto(base+'/tasks/concept/decisions/wait-concept');p.locator('input[value="2"]').check();p.locator('textarea[name=rationale]').fill('One deployment')
 p.get_by_role('button',name='Choose and continue').click();expect(p.locator('#review-status')).to_contain_text('Decision was accepted')
 expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled();assert len(state['posts'])==1

def test_semantic_findings_review_shows_choices_and_never_defaults(page):
 p,base,root=page;control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'FD-1','path':['constraints',0],'reason':'Repeats a need','kind':'semantic','original':'Fast app'}]}}}}
 posts=[]
 def route(r):
  if r.request.method=='POST':posts.append(r.request.post_data_json);r.fulfill(json={'status':'accepted'})
  else:r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None})
 p.route('**/api/tasks/concept**',route)
 p.goto(base+'/tasks/concept/decisions/wait-concept');expect(p.locator('h1')).to_have_text('Review flagged inputs')
 assert p.locator('input[name="action-0"]:checked').count()==0;expect(p.locator('select')).to_have_count(0);expect(p.get_by_text('Repeats a need')).to_be_visible()
 p.screenshot(path=str(root/'semantic-review-desktop.png'),full_page=True);p.mouse.wheel(0,120);p.wait_for_timeout(300);p.screenshot(path=str(root/'semantic-review-sticky.png'));p.evaluate('window.scrollTo(0,0)')
 p.set_viewport_size({'width':390,'height':844});p.screenshot(path=str(root/'semantic-review-mobile.png'),full_page=True)
 p.locator('input[name="action-0"][value="accept"]').check();p.get_by_role('button',name='Apply decisions and continue').click();expect(p.locator('#semanticstatus')).to_contain_text('Decisions recorded')
 assert json.loads(posts[0]['answer'])['decisions'][0]['action']=='accept'
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')

def test_approval_register_flags_missing_data_and_records_explicit_answer(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 register={'items':[{'item_id':'FD-style','data_class':'styling','status':'open','question':'What styling?','owner':'founder','blocks':['UI']}],'advisories':[]}
 state['current']['decision']['context']['evidence']['intent_model']['founder_register']=register
 p.goto(base+'/tasks/concept/decisions/wait-review');expect(p.get_by_text('Missing data and open founder inputs')).to_be_visible()
 p.locator('[data-founder-status]').select_option('decided');p.locator('[data-founder-answer]').fill('Blue and white');p.locator('[data-founder-reason]').fill('Calm')
 p.screenshot(path=str(root/'founder-register-desktop.png'),full_page=True)
 p.get_by_role('button',name='Continue',exact=True).click();expect(p.locator('#review-status')).to_contain_text('Decision saved')
 assert state['posts'][0]['founder_inputs'][0]['decision']=='Blue and white'

def test_semantic_draft_comment_item_links_and_historical_controls(page):
 p,base,root=page;control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'FD-1','reason':'Needs owner decision','kind':'semantic','original':'Fast app'}]}}}}
 state={'draft':None,'comments':[],'answers':[],'actionable':True}
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if r.request.method=='POST':
   data=r.request.post_data_json
   if path.endswith('/drafts'):state['draft']=data;r.fulfill(json={'draft_id':'semantic-draft'});return
   if path.endswith('/comments'):state['comments'].append({'actor':'founder','comment':data['comment']});r.fulfill(json={'comment_id':1});return
   state['answers'].append(data);r.fulfill(json={'status':'accepted'});return
  r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':state['actionable'],'draft':state['draft'],'comments':state['comments']})
 p.route('**/api/tasks/concept**',route)
 p.goto(base+'/tasks/concept/decisions/wait-concept?step=findings&item=FD-1')
 p.locator('input[name="action-0"][value="replace"]').check();p.locator('.replace-fields input[type=text]').fill('Recorded status');p.locator('[name="rationale-0"]').fill('Founder wording')
 p.get_by_role('button',name='Save draft',exact=True).click();expect(p.locator('#semanticstatus')).to_contain_text('Draft saved');link=p.url
 assert 'item=FD-1' in link and 'draft=semantic-draft' in link
 p.reload();expect(p.locator('input[name="action-0"][value="replace"]')).to_be_checked();expect(p.locator('.replace-fields input[type=text]')).to_have_value('Recorded status')
 p.locator('[name="comment"]').fill('Keep this note');p.get_by_role('button',name='Save comment only').click();expect(p.locator('#semantic-comments')).to_contain_text('Keep this note');assert not state['answers']
 p.set_viewport_size({'width':390,'height':844});p.evaluate('window.scrollTo(0,0)');p.screenshot(path=str(root/'semantic-draft-mobile.png'),full_page=True)
 state['actionable']=False;p.reload();expect(p.locator('input[name="action-0"][value="replace"]')).to_be_disabled();expect(p.get_by_role('button',name='Save draft',exact=True)).to_be_disabled()


def _flag_page(p,base,findings):
 control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':findings}}}}
 posts=[]
 def route(r):
  if r.request.method=='POST':posts.append(r.request.post_data_json);r.fulfill(json={'status':'accepted'})
  else:r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None})
 p.route('**/api/tasks/concept**',route);p.goto(base+'/tasks/concept/decisions/wait-concept');return posts

def _f(i,item,path,original,kind='semantic'):return {'finding_id':f'f{i}','item_id':item,'path':path,'reason':f'Reason {i}','kind':kind,'original':original}

def test_semantic_titles_are_meaningful_and_options_have_tooltips(page):
 p,base,root=page
 _flag_page(p,base,[_f(0,'constraint:0',['constraints',0],'Deployment is mandated: build with GitHub Actions and deploy on GitHub Pages (turn 0)'),_f(1,'FR-9',['functional',3],{'requirement_id':'FR-9','statement':'The app shall sync nightly'})])
 expect(p.locator('.semantic-item h2')).to_have_count(2);titles=p.locator('.semantic-item h2').all_inner_texts()
 assert titles==['Constraint: Deployment is mandated','Functional requirement FR-9: The app shall sync nightly'],titles
 assert 'constraint:0' not in p.locator('.semantic-page').inner_text()
 expect(p.locator('.semantic-item h2 a')).to_have_count(0);p.locator('#semantic-0').screenshot(path=str(root/'semantic-title-card.png'))
 expect(p.locator('.semantic-item blockquote').first).to_contain_text('build with GitHub Actions')
 for value,words in (('accept','Keep this exactly'),('remove','Delete this item'),('replace','Rewrite this item')):
  assert words in p.locator(f'input[name="action-0"][value="{value}"]').locator('xpath=..').get_attribute('title')
 p.locator('input[name="action-0"][value="remove"]').check();expect(p.locator('#caption-0')).to_contain_text('Delete this item');expect(p.locator('#semantic-count')).to_have_text('1 of 2 decided')

def test_semantic_header_and_footer_stay_visible_and_structural_cannot_be_kept(page):
 p,base,root=page
 _flag_page(p,base,[_f(i,f'constraint:{i}',['constraints',i],'Item number %d '%i+'words '*30,'structural' if i==0 else 'semantic') for i in range(12)])
 expect(p.locator('input[name="action-0"][value="accept"]')).to_be_disabled();expect(p.locator('input[name="action-1"][value="accept"]')).to_be_enabled()
 p.mouse.wheel(0,2500);p.wait_for_timeout(300)
 vh=p.viewport_size['height']
 head=p.locator('.semantic-head').bounding_box();foot=p.locator('.semantic-footer').bounding_box()
 assert 0<=head['y']<200 and head['y']+head['height']>0
 assert foot['y']+foot['height']<=vh+1 and foot['y']>=0
 expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_in_viewport()

def test_replace_shows_typed_fields_not_json_and_posts_object(page):
 p,base,root=page
 orig={'requirement_id':'FR-2','statement':'Sync nightly','priority':2,'must':True,'tags':['a','b']}
 posts=_flag_page(p,base,[_f(0,'FR-2',['functional',0],orig)])
 expect(p.locator('.replace-fields')).to_be_hidden();p.locator('input[name="action-0"][value="replace"]').check()
 expect(p.locator('.replace-fields')).to_be_visible();expect(p.locator('textarea[name="replacement-0"]')).to_have_count(0)
 expect(p.locator('[data-path=\'["requirement_id"]\']')).to_have_attribute('readonly','')
 p.locator('[data-path=\'["statement"]\']').fill('Sync every night');p.locator('[data-path=\'["priority"]\']').fill('5');p.locator('[data-path=\'["must"]\']').uncheck();p.locator('[data-path=\'["tags"]\']').fill('x\ny\nz')
 p.mouse.wheel(0,250);p.wait_for_timeout(300);p.screenshot(path=str(root/'semantic-replace-desktop.png'))
 p.set_viewport_size({'width':390,'height':844});p.mouse.wheel(0,500);p.wait_for_timeout(300);p.screenshot(path=str(root/'semantic-replace-mobile.png'))
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 p.get_by_role('button',name='Apply decisions and continue').click();expect(p.locator('#semanticstatus')).to_contain_text('Decisions recorded')
 rep=json.loads(posts[0]['answer'])['decisions'][0]['replacement']
 assert rep=={'requirement_id':'FR-2','statement':'Sync every night','priority':5,'must':False,'tags':['x','y','z']},rep
 p.set_viewport_size({'width':1280,'height':800})

def test_replace_rejects_bad_number(page):
 p,base,root=page
 posts=_flag_page(p,base,[_f(0,'FR-2',['functional',0],{'priority':2})])
 p.locator('input[name="action-0"][value="replace"]').check();p.locator('[data-path=\'["priority"]\']').fill('');p.get_by_role('button',name='Apply decisions and continue').click()
 expect(p.locator('#semanticstatus')).to_contain_text('must be a number');assert not posts

def test_phase_keys_have_human_labels():
 import subprocess,shutil,pathlib
 node=shutil.which('node')
 if not node:__import__('pytest').skip('node not available')
 web=pathlib.Path(__file__).resolve().parents[1]/'src/delivery_api/web/format.js'
 src=web.read_text().replace("import {esc} from './client-core.js';","const esc=s=>String(s);")
 out=subprocess.run([node,'--input-type=module','-e',src+";console.log(JSON.stringify(['intent','conops','needs','srs','architecture','specs','tests'].map(humanLabel)))"],capture_output=True,text=True,timeout=30)
 assert out.returncode==0,out.stderr
 assert json.loads(out.stdout)==['Start','Concept of Operations','Stakeholder Needs','Requirements','Architecture','Specifications','Tests']

def test_title_without_colon_uses_first_words():
 import subprocess,shutil,pathlib
 node=shutil.which('node')
 if not node:__import__('pytest').skip('node not available')
 web=pathlib.Path(__file__).resolve().parents[1]/'src/delivery_api/web/semantic-review-ui.js'
 src=web.read_text().replace("import {esc} from './client-core.js';","const esc=s=>String(s);").replace("import {humanLabel} from './format.js';","const humanLabel=s=>String(s);")
 out=subprocess.run([node,'--input-type=module','-e',src+";console.log(findingTitle({path:['constraints',0],original:'Users must be able to export every report as PDF without signing in'}))"],capture_output=True,text=True,timeout=30)
 assert out.returncode==0,out.stderr
 assert out.stdout.strip()=='Constraint: Users must be able to export…'

def _slow_flag_page(p,base,fail=False):
 control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[_f(0,'constraint:0',['constraints',0],'Deployment is mandated: build with GitHub Actions (turn 0)')]}}}}
 held=[]
 def route(r):
  if r.request.method=='POST':held.append(r)
  else:r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None})
 p.route('**/api/tasks/concept**',route);p.goto(base+'/tasks/concept/decisions/wait-concept');return held

def test_apply_shows_busy_then_success_feedback(page):
 p,base,root=page;held=_slow_flag_page(p,base)
 p.locator('input[name="action-0"][value="remove"]').check();btn=p.get_by_role('button',name='Apply decisions and continue');btn.click()
 expect(p.locator('#semanticstatus')).to_contain_text('Applying your decisions');expect(p.locator('#semanticform button[type=submit]')).to_contain_text('Applying decisions');expect(p.locator('#semanticform button[type=submit]')).to_be_disabled();expect(p.locator('#semanticform .waiting-spinner')).to_be_visible()
 p.screenshot(path=str(root/'semantic-applying.png'))
 expect(p.locator('#semanticstatus')).to_contain_text('1-2 minutes',timeout=9000)
 for _ in range(50):
  if held:break
  p.wait_for_timeout(100)
 held[0].fulfill(json={'status':'accepted'})
 expect(p.locator('#semanticstatus')).to_contain_text('The run is resuming');expect(p.locator('#semanticform button[type=submit]')).to_have_text('Decisions applied')
 p.screenshot(path=str(root/'semantic-applied.png'))

def test_apply_failure_is_visible_and_recoverable(page):
 p,base,root=page;held=_slow_flag_page(p,base)
 p.locator('input[name="action-0"][value="remove"]').check();p.get_by_role('button',name='Apply decisions and continue').click()
 for _ in range(50):
  if held:break
  p.wait_for_timeout(100)
 held[0].fulfill(status=500,json={'detail':'Engine unavailable'})
 expect(p.locator('#semanticstatus')).to_contain_text('Could not apply your decisions');expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_enabled()
 p.screenshot(path=str(root/'semantic-apply-error.png'))
