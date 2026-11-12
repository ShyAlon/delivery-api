"""Browser contracts for typed ConOps pages; no model calls or user accounts."""
import tempfile
from pathlib import Path as _ScreenshotPath
SCREENSHOTS = _ScreenshotPath(tempfile.mkdtemp(prefix='delivery-browser-evidence-'))
import json
import time
from pathlib import Path
import pytest
from playwright.sync_api import expect

def review(wait='wait-concept',purpose='conops_concept'):
 evidence={'conops_draft':{'stakeholder_context':[{'stakeholder':'Runner','goals':['Fast habit capture'],'operating_environment':'Phone, often offline'}],'operational_scenarios':[{'name':'Log a habit','operational_flow':['Open the app','Record points'],'success_outcome':'Points sync when online'}],'concept_options':[]}}
 if purpose=='interview_approval':evidence={'intent_model':{'problem_statement':'Runners need simple habit tracking','target_users':['Runner']},'conops':{'selected_concept':'PWA','scenarios':[{'name':'Log a habit offline'}]}}
 options=[{'value':'1','label':'Native app','summary':'Installed client with native APIs','advantages':['Native device access'],'disadvantages':['Store releases']},{'value':'2','label':'PWA','summary':'One installable web product','advantages':['One deployment'],'disadvantages':['Platform limits']}]
 if purpose=='interview_approval':options=[{'value':1,'label':'Proceed'},{'value':0,'label':'Edit'}]
 return {'version':2,'task_id':'concept','phase_key':'conops','owner':'engine','mode':'waiting_input','kind':'choice' if purpose=='conops_concept' else 'approval','wait_id':wait,'revision':'engine:s:3' if purpose=='conops_concept' else 'engine:s:5','precondition':{'session_id':'s','session_sequence':3 if purpose=='conops_concept' else 5},'options':options,'prompt':'Choose which concept should execute your intent.' if purpose=='conops_concept' else 'Review your intent and ConOps.','decision':{'rationale_required':purpose=='conops_concept','context':{'decision_purpose':purpose,'evidence':evidence}}}

@pytest.fixture
def page(pg_browser,tmp_path):
 browser_page,base,workspace=pg_browser
 browser_page.set_viewport_size({'width':1365,'height':1000})
 yield browser_page,base,tmp_path

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
 p.goto(base+'/tasks/concept/decisions/wait-concept');expect(p.locator('h1')).to_have_text('Choose the Operating Concept')
 assert p.locator('input[name=choice]:checked').count()==0;assert p.locator('[role=dialog]').count()==0
 p.locator('input[value="2"]').check();p.locator('textarea[name=rationale]').fill('One deployment is enough');p.locator('textarea[name=comment]').fill('Check offline behavior')
 expect(p.locator('#draft-status')).to_contain_text('Unsaved')
 p.get_by_role('button',name='Save draft',exact=True).click();expect(p.locator('#draft-status')).to_contain_text('Draft saved')
 link=p.url;assert 'draft=saved-one' in link and 'item=2' in link
 p.reload();expect(p.locator('textarea[name=rationale]')).to_have_value('One deployment is enough');expect(p.locator('input[value="2"]')).to_be_checked()
 p.screenshot(path=str(root/'concept-desktop.png'),full_page=True)
 p.get_by_role('button',name='Choose and continue').click();expect(p.locator('h1')).to_have_text('Review Before Continuing');assert 'wait-review' in p.url; expect(p.locator('.context-box .gate-structured').first).to_be_attached();expect(p.locator('.review-fact')).to_have_count(0); p.screenshot(path=str(root/'final-review.png'),full_page=True)
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
 p.route('**/api/debug/collection*',lambda r:r.fulfill(json={'sections':[]}))
 p.goto(base+'/tasks/concept/decisions/wait-concept');p.locator('#accountbutton').click();p.get_by_role('menuitem',name='Debug',exact=True).click()
 expect(p.locator('.debug-dialog h2')).to_have_text('Debug');expect(p.locator('.debug-dialog')).to_contain_text('Browser capture: this tab since')
 with p.expect_download() as download:p.get_by_role('button',name='Download everything ZIP').click()
 download.value.save_as(str(root/'debug-evidence.zip'))
 expect(p.locator('#debugstatus')).to_contain_text('Downloaded requi-everything.zip')
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
 p.get_by_role('button',name='Choose and continue').click()
 # Recovery contract: the committed answer is never resubmitted; the page shows
 # the recovery status and keeps the submit disabled while it retries/navigates.
 expect(p.locator('#review-status')).to_contain_text('Decision saved')
 expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled();assert len(state['posts'])==1

def test_semantic_findings_review_shows_choices_and_never_defaults(page):
 p,base,root=page;control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'FD-1','path':['constraints',0],'reason':'Repeats a need','kind':'semantic','original':'Fast app'}]}}}}
 posts=[]
 def route(r):
  if r.request.method=='POST':posts.append(r.request.post_data_json);r.fulfill(json={'status':'accepted'})
  else:r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None})
 p.route('**/api/tasks/concept**',route)
 p.goto(base+'/tasks/concept/decisions/wait-concept');expect(p.locator('h1')).to_have_text('Review Flagged Inputs')
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
 p.goto(base+'/tasks/concept/decisions/wait-review');expect(p.get_by_text('Missing Data and Open Founder Inputs')).to_be_visible();expect(p.locator('body')).not_to_contain_text('FD-style');expect(p.locator('select')).to_have_count(0)
 p.locator('.founder-register details').first.evaluate('d=>d.open=true');p.locator('[data-founder-group] label.pill',has_text='Answer It').click();p.locator('[data-founder-answer]').fill('Blue and white');p.locator('[data-founder-reason]').fill('Calm')
 p.set_viewport_size({'width':1249,'height':920});assert p.evaluate("(()=>{const r=document.querySelector('.founder-register').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&document.documentElement.scrollWidth<=innerWidth})()");assert p.evaluate("document.querySelector('.page-head').getBoundingClientRect().height<150");p.mouse.wheel(0,400);p.wait_for_timeout(200);assert p.evaluate("getComputedStyle(document.querySelector('.page-head')).position==='relative'")  # Shared document scroll replaces sticky heading.;p.screenshot(path=str(root/'founder-register-desktop.png'),full_page=True)
 p.get_by_role('button',name='Continue',exact=True).click();expect(p.locator('#review-status')).to_contain_text('Decision saved')
 assert state['posts'][0]['founder_inputs'][0]['decision']=='Blue and white'

def test_continue_names_unfinished_inputs_and_boxes_do_not_repeat_their_title(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 long_q='Which '+' '.join(['word%d'%i for i in range(40)])+'?'
 register={'items':[{'item_id':'FD-a','data_class':'styling','status':'open','question':'What styling?','owner':'founder','blocks':['UI']},{'item_id':'FD-b','data_class':'flows','status':'open','question':long_q,'owner':'founder','blocks':[]}],'advisories':[{'advisory_id':'ADV-1','advisory_class':'rights','statement':'Confirm rights to third-party content.','owner':'founder','status':'open','source':'x','affected_ids':[],'validation_expectation':'Written confirmation on file.'}]}
 state['current']['decision']['context']['evidence']['intent_model']['founder_register']=register
 p.set_viewport_size({'width':1249,'height':920});p.goto(base+'/tasks/concept/decisions/wait-review')
 boxes=p.locator('.founder-register details');expect(boxes).to_have_count(3)
 adv=boxes.nth(2);adv.evaluate('d=>d.open=true');body=adv.locator('.context-body').inner_text()
 assert 'Confirm rights to third-party content.' not in body and 'How to verify: Written confirmation on file.' in body
 boxes.nth(1).evaluate('d=>d.open=true');assert 'word39' in boxes.nth(1).locator('.context-body').inner_text()
 expect(boxes.nth(0).locator('.state-badge')).to_have_text('Leave Open')
 boxes.nth(0).evaluate('d=>d.open=true');p.locator('[data-founder-group]').first.locator('label.pill').nth(2).click();expect(boxes.nth(0).locator('.state-badge')).to_have_text('Decide Later')
 boxes.nth(0).evaluate('d=>d.open=false')
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_disabled()
 status=p.locator('#review-status');checklist=p.locator('#decision-checklist');expect(checklist).to_contain_text('What styling?');expect(checklist).to_contain_text('needs a reason')
 checklist.locator('[data-jump]').first.click()
 assert boxes.nth(0).evaluate('d=>d.open');expect(p.locator('[data-founder-reason]').first).to_be_focused();assert not state['posts']
 p.screenshot(path=str(root/'needs-input.png'));p.set_viewport_size({'width':460,'height':900});p.locator('#save-draft').scroll_into_view_if_needed();p.screenshot(path=str(root/'right-panel-narrow.png'));p.set_viewport_size({'width':1249,'height':920})
 p.locator('[data-founder-reason]').first.fill('Ask the designer');p.get_by_role('button',name='Continue',exact=True).click()
 expect(status).to_contain_text('Decision saved');assert state['posts'][0]['founder_inputs'][0]['status']=='deferred'

def test_rework_sends_founder_answers_and_register_shows_history(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 register={'items':[{'item_id':'FD-style','data_class':'styling','status':'decided','decision':'Blue','reason':'Calm','question':'What styling?','owner':'ann','blocks':['UI'],'history':[{'at':'2026-10-03T10:00:00+00:00','actor':'ann','status':'decided','decision':'Blue','reason':'Calm'}]}],'advisories':[]}
 state['current']['decision']['context']['evidence']['intent_model']['founder_register']=register
 p.goto(base+'/tasks/concept/decisions/wait-review')
 p.locator('.founder-register details').first.evaluate('d=>d.open=true')
 expect(p.locator('.founder-history')).to_contain_text('Answered by ann');expect(p.locator('.founder-history')).to_contain_text('Reason: Calm')
 for name in ['Answer It','Decide Later','Leave Open']:expect(p.locator('[data-founder-group] label.pill',has_text=name)).to_have_count(1)
 p.locator('[data-founder-group] label.pill',has_text='Decide Later').click();p.locator('[data-founder-reason]').fill('Ask the designer')
 p.get_by_role('button',name='Request rework').click();p.locator('textarea[name=guidance]').fill('More scenarios');p.locator('#confirm-rework').click()
 expect(p.locator('#review-status')).to_contain_text('Decision saved')
 sent=state['posts'][0];assert sent['answer'].startswith('0');assert sent['founder_inputs']==[{'item_id':'FD-style','status':'deferred','decision':None,'reason':'Ask the designer'}]

def test_reason_is_optional_when_the_input_blocks_nothing(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 register={'items':[{'item_id':'FD-free','data_class':'content','status':'open','question':'What tone?','owner':'founder','blocks':[]},{'item_id':'FD-blk','data_class':'flows','status':'open','question':'What flow?','owner':'founder','blocks':['UI']}],'advisories':[]}
 state['current']['decision']['context']['evidence']['intent_model']['founder_register']=register
 p.goto(base+'/tasks/concept/decisions/wait-review')
 boxes=p.locator('.founder-register details');boxes.nth(0).evaluate('d=>d.open=true');boxes.nth(1).evaluate('d=>d.open=true')
 expect(boxes.nth(0)).to_contain_text('Reason (optional)');expect(boxes.nth(1)).to_contain_text('Reason (required - this blocks later work)')
 g=p.locator('[data-founder-group]')
 g.nth(0).locator('label.pill').nth(1).click();boxes.nth(0).locator('[data-founder-answer]').fill('Friendly')
 g.nth(1).locator('label.pill').nth(2).click()
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_disabled()
 status=p.locator('#review-status');checklist=p.locator('#decision-checklist');expect(checklist).to_contain_text('What flow?');expect(checklist).not_to_contain_text('What tone?');assert not state['posts']
 boxes.nth(1).locator('[data-founder-reason]').fill('Ask designer');p.get_by_role('button',name='Continue',exact=True).click()
 expect(status).to_contain_text('Decision saved')
 sent={x['item_id']:x for x in state['posts'][0]['founder_inputs']};assert sent['FD-free']['reason']=='' and sent['FD-free']['decision']=='Friendly' and 'reasonRequired' not in sent['FD-free']

def test_review_gate_hands_off_to_task_view_after_saving(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 p.goto(base+'/tasks/concept/decisions/wait-review')
 p.get_by_role('button',name='Continue',exact=True).click()
 status=p.locator('#review-status');expect(status).to_contain_text('Decision saved');expect(status.get_by_role('link',name='Open now')).to_have_attribute('href','/tasks/concept/view')
 p.screenshot(path=str(root/'review-handoff.png'))
 p.wait_for_url('**/tasks/concept/view',timeout=5000)

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
 assert p.locator('.semantic-head').evaluate('e=>getComputedStyle(e).position')=='relative'  # Static heading, persistent bottom action only.
 assert foot['y']+foot['height']<=vh+1 and foot['y']>=0
 expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_in_viewport()

def test_replace_shows_typed_fields_not_json_and_posts_object(page):
 p,base,root=page
 orig={'requirement_id':'FR-2','statement':'Sync nightly','priority':2,'must':True,'tags':['a','b']}
 posts=_flag_page(p,base,[_f(0,'FR-2',['functional',0],orig)])
 expect(p.locator('.flag-original')).to_contain_text('Sync nightly');expect(p.locator('.flag-original')).not_to_contain_text('FR-2')  # record IDs are not shown to founders
 expect(p.locator('.replace-fields')).to_be_hidden();p.locator('input[name="action-0"][value="replace"]').check()
 expect(p.locator('.replace-fields')).to_be_visible();expect(p.locator('textarea[name="replacement-0"]')).to_have_count(0)
 expect(p.locator('[data-path=\'["requirement_id"]\']')).to_have_count(0)  # IDs are hidden from founders; the posted object below still carries it
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

def test_phase_keys_have_human_labels(page):
 p,base,root=page
 p.goto(base+'/')
 labels=p.evaluate("async()=>{const {humanLabel}=await import('/assets/format.js');return ['intent','conops','needs','srs','architecture','specs','tests'].map(humanLabel)}")
 assert labels==['Start','Concept of Operations','Stakeholder Needs','Requirements','Architecture','Specifications','Tests']

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
 expect(p.locator('#semanticstatus')).to_contain_text('Saving your decisions');expect(p.locator('#semanticform button[type=submit]')).to_contain_text('Applying decisions');expect(p.locator('#semanticform button[type=submit]')).to_be_disabled();expect(p.locator('#semanticform .waiting-spinner')).to_be_visible()
 p.screenshot(path=str(root/'semantic-applying.png'))
 expect(p.locator('#semanticstatus')).to_contain_text('Estimating');assert '1-2 minutes' not in p.locator('#semanticstatus').inner_text()
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
def _concept_page(p,base,long_options=False,comments=None,tall=False):
 control=review()
 if long_options:
  control['options']=[{'value':'1','label':'The Installed Practice Toy: A Standalone Home-Screen Music App','summary':'Under this operating concept the product is a single, self-contained music toy that the 7-year-old installs once onto the iPhone or iPad home screen and then owns entirely - no sign-in, no setup wizard, no adult in the loop (turn 4). '*4,'advantages':['Zero adult in the loop at any point: no sign-in, no setup wizard (turns 0, 6)'],'disadvantages':['Progress lives only in browser local storage (turns 12, 18)']},{'value':'2','label':'A shared family workspace','summary':'Short','advantages':[],'disadvantages':[]}]
 sc=[{'stakeholder':f'Stakeholder {i}','role':'Tracks habits and reviews weekly progress (turn 2)','goals':['Quick capture','Weekly summary','Share with a friend'],'operating_environment':'Phone, often offline, on a commuter train with poor signal','source_turns':[0,1,2]} for i in range(2)]
 os_=[{'scenario_id':f'OS-{i}','name':f'Scenario {i}','trigger':'Habit completed','actors':['Runner'],'operational_flow':['Open app','Record points','Sync later'],'success_outcome':'Points sync','exceptions':['Offline'],'source_turns':[0]} for i in range(3)]
 control['decision']['context']['evidence']['conops_draft']['stakeholder_context']=sc
 control['decision']['context']['evidence']['conops_draft']['operational_scenarios']=os_
 control['decision']['context']['evidence']['conops_draft']['contract_version']='1.2.3'
 if tall:
  # Deterministic deep scroll WITHOUT changing the box contract: same five
  # context boxes, but with long inner content so the page really scrolls past
  # the sticky controls' engagement point in every font environment (the y>=83
  # sticky contract cannot be exercised when the document clamps the scroll).
  draft=control['decision']['context']['evidence']['conops_draft']
  for sh in draft['stakeholder_context']:sh['goals']=sh['goals']*30
  for sc in draft['operational_scenarios']:sc['operational_flow']=sc['operational_flow']*30
 p.route('**/api/tasks/concept**',lambda r:r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None,'comments':comments or []}))
 p.goto(base+'/tasks/concept/decisions/wait-concept')
 if tall:p.evaluate('document.fonts.ready.then(()=>1)')

def test_concept_context_is_collapsed_without_debug_fields_and_does_not_overlap(page):
 p,base,root=page;_concept_page(p,base,tall=True)
 text=p.locator('.gate-evidence').inner_text()
 assert 'source turns' not in text.lower() and 'contract version' not in text.lower() and '1.2.3' not in text
 boxes=p.locator('.context-box');assert boxes.count()==5
 assert p.locator('.context-box[open]').count()==0
 expect(p.locator('.context-box summary').first).to_have_text('Stakeholder 0 · Tracks habits and reviews weekly progress')
 expect(p.get_by_text('Quick capture').first).to_be_hidden()
 p.locator('.context-box summary').first.click();expect(p.get_by_text('Quick capture').first).to_be_visible()
 p.locator('input[name="choice"][value="2"]').check();p.locator('textarea[name=rationale]').fill('One deployment');p.locator('textarea[name=comment]').fill('A note')
 p.screenshot(path=str(root/'concept-collapsed.png'))
 for y in (0,500,1200):
  p.evaluate(f'window.scrollTo(0,{y})');p.wait_for_timeout(150)
  ev=p.locator('.gate-evidence').bounding_box();ct=p.locator('.gate-controls').bounding_box();head=p.locator('.page-head').bounding_box()
  assert head['y']+head['height']<=ev['y']+ev['height'] and (y>0 or head['y']>=0)
  if y>0:assert p.locator('.gate-controls').evaluate('e=>getComputedStyle(e).position')=='static'  # Shared single document flow.
  assert ev['x']+ev['width']<=ct['x']+1,(y,ev,ct)
 p.evaluate('window.scrollTo(0,300)');p.wait_for_timeout(150);p.screenshot(path=str(root/'concept-accepted-scrolled.png'))


def test_selected_concept_card_stays_inside_its_column_with_long_text(page):
 p,base,root=page;p.set_viewport_size({'width':1249,'height':920});_concept_page(p,base,long_options=True)
 p.locator('input[name="choice"][value="1"]').check();p.locator('textarea[name=comment]').fill('note')
 for y in (0,700,1400):
  p.evaluate(f'window.scrollTo(0,{y})');p.wait_for_timeout(150)
  ev=p.locator('.gate-evidence').bounding_box();card=p.locator('.decision-option.selected').bounding_box();ct=p.locator('.gate-controls').bounding_box()
  assert card['x']+card['width']<=ev['x']+ev['width']+1,(y,card,ev)
  assert card['x']+card['width']<=ct['x']+1,(y,card,ct)
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 p.evaluate('document.querySelector(".decision-option.selected").scrollIntoView({block:"center"})');p.wait_for_timeout(200)
 p.screenshot(path=str(root/'concept-long-selected.png'))


def test_saved_comments_render_and_placeholder_clears_and_turn_refs_are_hidden(page):
 p,base,root=page;_concept_page(p,base,long_options=True,comments=[{'actor':'shy','comment':'First note'},{'actor':'shy','comment':'Second note'}])
 panel=p.locator('#review-comments');expect(panel).to_contain_text('First note');expect(panel).to_contain_text('Second note');expect(panel.locator('.no-comments')).to_have_count(0)
 body=p.locator('.gate-evidence').inner_text();assert '(turn' not in body and 'turn 4' not in body,body
 expect(p.locator('.context-box summary').first).to_have_text('Stakeholder 0 · Tracks habits and reviews weekly progress')
 _concept_page(p,base);expect(p.locator('#review-comments .no-comments')).to_have_text('No comments attached yet.')
 p.locator('textarea[name=comment]').fill('Fresh note');p.get_by_role('button',name='Save comment only').click()
 expect(p.locator('#review-comments')).to_contain_text('Fresh note');expect(p.locator('#review-comments .no-comments')).to_have_count(0)
 p.screenshot(path=str(root/'concept-comments.png'))


def _stack_review(decided=()):
 slots=[{'key':'database_engine','brief':'Which store fits the data?','options':['SQLite','PostgreSQL'],'founder_decision':({'text':'SQLite','reason':'One user','actor':'x','at':'t'} if 'database_engine' in decided else None),'prefill':None},
        {'key':'queue','brief':'How are jobs queued?','options':[],'founder_decision':None,'prefill':{'text':'In process','reason':'Small','actor':'x','at':'t'}}]
 gate={'gate_key':'gate-architecture','label':'architecture','after_phase':'architecture','state':'waiting','is_open':False,'options':['Proceed']}
 art={'phase_key':'architecture','name':'architecture.json','ref':'/x/architecture.json','present':True,'valid':True,'stale':False,'generation':1}
 task={'task_id':'t1','title':'Habit tracking','gates':[gate],'phases':[{'phase_key':'architecture','label':'Architecture'},{'phase_key':'specs','label':'Item specifications'}],'artifacts':[art],'phase_config':{},'generation':1}
 return {'review':{'revision':'gate:r','phase_key':'architecture','task':task,'documents':[{'name':'architecture.json','view':{'content':'{"components":[]}','format':'json'}}],'semantic_review':None,'stack_decisions':{'revision':'a'*64,'slots':slots}},'actionable':True,'draft':None,'comments':[]}

def test_architecture_technical_default_is_readable_nonblocking_and_change_optional(page):
 p,base,root=page;posts=[]
 framing={"title": "Storing Your Data", "purpose": "Keeps the product's records between visits.", "default_value": "SQLite", "reason": "A single local user needs a small store.", "requirement_ids": ["R-1"], "options": [{"value": "SQLite", "label": "One Local Store", "implication": "Simple to run; a shared server may be needed later."}, {"value": "PostgreSQL", "label": "A Shared Store", "implication": "Supports shared access but adds a server to manage."}], "provenance": "design_proposal", "research_status": "pending"}
 data=_stack_review();data['review']['stack_decisions']['slots']=data['review']['stack_decisions']['slots'][:1]
 slot=data['review']['stack_decisions']['slots'][0];slot['technical_choice']=framing;slot['brief']='SECRET INTERNAL INVESTIGATION BLOB'
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/stack-decisions'):posts.append(r.request.post_data_json);r.fulfill(json={'status':'saved'});return
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 panel=p.locator('#stack-panel');expect(panel).to_be_attached()
 expect(p.locator('[data-action=approve]')).to_be_enabled()
 expect(panel.locator('summary').first).to_have_text('Technical Choices')
 panel.locator('summary').first.click()
 expect(panel).to_contain_text('One Local Store');expect(panel).to_contain_text('Research Pending')
 assert 'SECRET INTERNAL' not in panel.inner_text()
 expect(panel.locator('.stack-change')).not_to_have_attribute('open','')
 p.screenshot(path=str(root/'technical-default-desktop.png'),full_page=True)
 p.set_viewport_size({'width':390,'height':844});assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 p.screenshot(path=str(root/'technical-default-mobile.png'),full_page=True)
 panel.locator('.stack-change summary').click();panel.get_by_role('button',name='A Shared Store',exact=True).click()
 panel.get_by_role('button',name='Save Change',exact=True).click()
 expect(panel.locator('#stack-status')).to_contain_text('Change Saved')
 assert posts==[{'revision':'a'*64,'decisions':[{'key':'database_engine','text':'PostgreSQL','reason':''}]}]
 expect(p.locator('[data-action=approve]')).to_be_disabled()

def test_reference_review_shows_precise_reason_and_cited_quote(page):
 p,base,root=page
 original={'name':'D&D Beyond','kind':'reference_product','source_turns':[7]}
 finding=_f(0,'reference_name:0',['reference_names',0],original)
 finding['reason']='A cited turn is the interviewer, not you. Interviewer examples are not founder evidence.\nTurn 7 (Interviewer): "Should it integrate with D&D Beyond?"'
 _flag_page(p,base,[finding])
 expect(p.locator('.flag-reason')).to_have_text('A cited turn is the interviewer, not you. Interviewer examples are not founder evidence.')
 box=p.locator('.cited-evidence');expect(box).to_be_visible();assert not box.evaluate('e=>e.open')
 box.locator('summary').click()
 expect(box.locator('blockquote')).to_have_text('Turn 7 (Interviewer): "Should it integrate with D&D Beyond?"')
 p.locator('.semantic-item').scroll_into_view_if_needed()
 p.screenshot(path='/tmp/reference-review-desktop-viewport.png')
 p.screenshot(path='/tmp/reference-review-desktop.png',full_page=True)
 p.set_viewport_size({'width':390,'height':844})
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 assert p.locator('.semantic-footer').evaluate('e=>getComputedStyle(e).position') == 'fixed'  # Shared current-action strip stays visible on phone.
 p.locator('.semantic-item').scroll_into_view_if_needed()
 p.screenshot(path='/tmp/reference-review-mobile-viewport.png')
 p.screenshot(path='/tmp/reference-review-mobile.png',full_page=True)

def _return_to_tab_until(p,locator,text,timeout=12000):
 # The header refreshes when the user returns to the tab; driving the app's own
 # visibility trigger keeps the intentional 30s poll intact instead of racing it.
 # A refresh already in flight debounces the next one, so a patient user may
 # return more than once; the required text never changes.
 deadline=time.monotonic()+timeout/1000
 while True:
  p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
  try:expect(locator).to_have_text(text,timeout=500);return
  except AssertionError:
   if time.monotonic()>deadline:raise

@pytest.mark.parametrize('screen', ['concept','review','semantic'])
def test_shared_session_cost_above_every_decision_screen(page,screen):
 p,base,root=page;state=fake(p)
 if screen=='review':state['current']=review('wait-review','interview_approval')
 elif screen=='semantic':
  state['current']=review();state['current']['kind']='semantic_review'
  state['current']['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[_f(0,'x',['constraints',0],'Fast app')]}}}}
 totals={'price':0.73,'price_usd':0.73}
 p.route('**/api/tasks/concept/cost',lambda r:r.fulfill(json=totals))
 p.goto(base+'/tasks/concept/decisions/'+state['current']['wait_id'])
 cost=p.locator('header #sessioncost');expect(cost).to_have_text('0.73 credits')
 totals.update(price=0.9,price_usd=0.9)
 _return_to_tab_until(p,cost,'0.90 credits')
 assert p.locator('#sessioncost').count()==1
 p.screenshot(path=f'/tmp/global-cost-{screen}-desktop.png')
 p.set_viewport_size({'width':390,'height':844});p.evaluate('window.scrollTo(0,0)')
 assert p.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 expect(cost).to_be_in_viewport();p.screenshot(path=f'/tmp/global-cost-{screen}-mobile.png')


def test_header_cost_unavailable_last_known_and_recovery(page):
 p,base,root=page;fake(p);state={'fail':True,'price':.73}
 def cost(r):
  if state['fail']:r.fulfill(status=503,json={'detail':'Unavailable'})
  else:r.fulfill(json={'price':state['price'],'price_usd':state['price']})
 p.route('**/api/tasks/concept/cost',cost)
 p.goto(base+'/tasks/concept/decisions/wait-concept');host=p.locator('#sessioncost')
 expect(host).to_have_text('Session Cost Unavailable')
 state['fail']=False;_return_to_tab_until(p,host,'0.73 credits')
 state['fail']=True;_return_to_tab_until(p,host,'0.73 credits · Last Known')
 state.update(fail=False,price=.9);_return_to_tab_until(p,host,'0.90 credits')


def test_gate_render_failure_stays_explained_after_checklist_refresh(page):
 p,base,root=page;data=_stack_review()
 data['review']['documents'][0]={'name':'architecture.json','error':'retained evidence unavailable'}
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 expect(p.locator('#stack-panel')).to_be_attached() # This calls updateDecision after failed render.
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 expect(p.locator('#decision-checklist')).to_contain_text('architecture.json could not be shown')
 expect(p.locator('[data-action=approve]')).to_have_attribute('aria-describedby','decision-checklist')
 p.locator('#decision-checklist button').first.click()
 p.screenshot(path=str(SCREENSHOTS/'gate-evidence-blocker-desktop.png'),full_page=True)
 p.set_viewport_size({'width':390,'height':844})
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 p.screenshot(path=str(SCREENSHOTS/'gate-evidence-blocker-mobile.png'),full_page=True)


def test_saved_stack_choice_checklist_explains_stale_evidence(page):
 p,base,root=page;data=_stack_review();posts=[]
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/stack-decisions'):posts.append(r.request.post_data_json);r.fulfill(json={'status':'saved'});return
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 panel=p.locator('#stack-panel');panel.locator('summary').first.click()
 panel.locator('.stack-change summary').first.click()
 panel.locator('textarea[name=stack-text-0]').fill('SQLite')
 panel.locator('.stack-save').first.click()
 expect(panel.locator('#stack-status')).to_contain_text('Change Saved')
 expect(p.locator('#decision-checklist')).to_contain_text('Your saved change updated the evidence')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 assert len(posts)==1


def test_required_next_model_has_checklist_blocker_and_jump(page):
 p,base,root=page;data=_stack_review()
 data['review']['stack_decisions']=None
 data['review']['task']['phase_config']={'specs':{'executor':'generator'}}
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/generator-default/specs'):r.fulfill(json={'model':None,'source':'selection required'});return
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[{'id':'fake','models':['reviewed-model'],'available':True}]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Choose the next phase model')
 expect(p.locator('.model-panel')).not_to_have_attribute('open','')
 p.locator('#decision-checklist [data-jump=".model-panel"]').click()
 expect(p.locator('.model-panel')).to_have_attribute('open','')
 expect(p.locator('select[name=next_model]')).to_be_visible()
 p.locator('select[name=next_model]').select_option('fake:reviewed-model')
 expect(p.locator('[data-action=approve]')).to_be_enabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Ready to continue')
 p.locator('select[name=next_model]').select_option('')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 p.screenshot(path=str(SCREENSHOTS/'gate-required-model-desktop.png'))
 p.set_viewport_size({'width':390,'height':844})
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 p.screenshot(path=str(SCREENSHOTS/'gate-required-model-mobile.png'))


def test_gate_checklist_jump_reveals_collapsed_target_and_moves_focus(page):
 p,base,root=page;data=_stack_review()
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 panel=p.locator('#stack-panel');expect(panel.locator(':scope > details')).not_to_have_attribute('open','')
 p.locator('#decision-checklist [data-jump="#stack-panel"]').click()
 expect(panel.locator(':scope > details')).to_have_attribute('open','')
 assert p.evaluate('document.activeElement.closest("#stack-panel")!==null')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 expect(panel.locator('textarea[name=stack-text-0]')).to_be_visible()
 expect(panel.locator('textarea[name=stack-text-0]')).to_be_focused()
 p.wait_for_timeout(500)
 p.screenshot(path=str(SCREENSHOTS/'gate-checklist-jump-desktop.png'))
 p.set_viewport_size({'width':390,'height':844})
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 p.locator('#decision-checklist [data-jump="#stack-panel"]').click()
 p.wait_for_timeout(500)
 expect(panel.locator('textarea[name=stack-text-0]')).to_be_in_viewport()
 p.screenshot(path=str(SCREENSHOTS/'gate-checklist-jump-mobile.png'))


def test_concept_shared_checklist_missing_choice_reason_and_jump(page):
 p,base,root=page;state=fake(p)
 p.goto(base+'/tasks/concept/decisions/wait-concept')
 expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Choose an operating concept')
 expect(p.locator('#decision-checklist')).to_contain_text('Explain why this concept fits')
 p.locator('#decision-checklist [data-jump=".decision-options"]').click()
 expect(p.locator('input[name=choice]').first).to_be_focused()
 p.locator('input[value="2"]').check()
 expect(p.locator('#decision-checklist')).not_to_contain_text('Choose an operating concept')
 p.locator('textarea[name=rationale]').fill('One deployment')
 expect(p.get_by_role('button',name='Choose and continue')).to_be_enabled()
 p.locator('textarea[name=rationale]').fill('')
 expect(p.get_by_role('button',name='Choose and continue')).to_be_disabled()
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 p.screenshot(path=str(SCREENSHOTS/'concept-checklist-desktop.png'))
 p.set_viewport_size({'width':390,'height':844})
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 p.screenshot(path=str(SCREENSHOTS/'concept-checklist-mobile.png'))
 assert not state['posts']


def test_closed_phase_change_draft_explains_regeneration_cost_without_commit(page):
 p,base,root=page;data=_stack_review();posts=[];data['actionable']=False;data['closed_revision_draft']=True
 data['review']['task']['gates'][0]['state']='decided'
 def route(r):
  path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
  if path.endswith('/revision-drafts'):
   body=r.request.post_data_json;posts.append({'path':path,'body':body})
   data['draft']={'kind':'closed_gate_revision','edited_text':body['edited_text']}
   r.fulfill(json={'draft_id':'closed-draft','committed':False,'dispatch_started':False});return
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 panel=p.locator('.closed-revision-draft')
 expect(panel).to_contain_text('regenerate this phase and affected downstream work')
 expect(panel).to_contain_text('incur extra cost');expect(panel).to_contain_text('not available yet')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 p.get_by_label('What should regeneration change?',exact=True).fill('Use offline-first storage throughout the document')
 p.get_by_role('button',name='Save proposed revision',exact=True).click()
 expect(p.locator('#closedrevisionstatus')).to_contain_text('approved result and downstream work are unchanged')
 assert len(posts)==1 and posts[0]['path'].endswith('/revision-drafts')
 assert posts[0]['body']['edited_text']=='Use offline-first storage throughout the document'
 p.reload();expect(p.locator('#closedrevisiontext')).to_have_value(posts[0]['body']['edited_text'])
 panel.scroll_into_view_if_needed();p.screenshot(path=str(SCREENSHOTS/'closed-regeneration-cost-desktop.png'))
 p.set_viewport_size({'width':390,'height':844});panel.scroll_into_view_if_needed();p.wait_for_timeout(250)
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 p.screenshot(path=str(SCREENSHOTS/'closed-regeneration-cost-mobile.png'))


def test_flagged_inputs_shared_checklist_explains_missing_choice_and_jump(page):
 p,base,root=page;held=_slow_flag_page(p,base)
 expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_disabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Choose what happens to Constraint')
 p.locator('#decision-checklist [data-jump="#semantic-0"]').click()
 expect(p.locator('input[name="action-0"]').first).to_be_focused()
 p.locator('input[name="action-0"][value="remove"]').check()
 expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_enabled()
 expect(p.locator('#decision-checklist')).to_contain_text('Ready to continue')
 assert not held
 p.screenshot(path=str(SCREENSHOTS/'findings-checklist-desktop.png'))
 p.set_viewport_size({'width':390,'height':844})
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 p.wait_for_timeout(600)
 expect(p.get_by_role('button',name='Apply decisions and continue')).to_be_in_viewport()
 p.screenshot(path=str(SCREENSHOTS/'findings-checklist-mobile.png'))


def test_final_approval_shared_checklist_answer_reason_draft_and_leave_open(page):
 p,base,root=page;state=fake(p);state['current']=review('wait-review','interview_approval')
 state['current']['decision']['context']['evidence']['intent_model']['founder_register']={'items':[{'item_id':'FD-style','data_class':'styling','status':'open','question':'What styling?','owner':'founder','blocks':['UI']}],'advisories':[]}
 p.goto(base+'/tasks/concept/decisions/wait-review')
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_enabled()
 box=p.locator('.founder-register details').first;box.evaluate('d=>d.open=true')
 p.locator('[data-founder-group] label.pill',has_text='Answer It').click()
 expect(p.locator('#decision-checklist')).to_contain_text('needs an answer')
 expect(p.locator('#decision-checklist')).to_contain_text('needs a reason')
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_disabled()
 expect(p.locator('#confirm-rework')).to_be_disabled()
 p.get_by_role('button',name='Save draft',exact=True).click();expect(p.locator('#draft-status')).to_contain_text('Draft saved')
 p.reload();expect(p.get_by_role('button',name='Continue',exact=True)).to_be_disabled()
 expect(p.locator('.founder-register .state-badge').first).to_have_text('Answer It')
 box=p.locator('.founder-register details').first;box.evaluate('d=>d.open=false')
 p.locator('#decision-checklist [data-jump]').first.click();expect(p.locator('[data-founder-answer]')).to_be_focused()
 assert box.evaluate('d=>d.open')
 p.locator('[data-founder-answer]').fill('Calm palette')
 p.locator('#decision-checklist').scroll_into_view_if_needed()
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_disabled()
 p.screenshot(path=str(SCREENSHOTS/'final-approval-checklist-desktop.png'))
 p.set_viewport_size({'width':390,'height':844});p.locator('#decision-checklist').scroll_into_view_if_needed();p.wait_for_timeout(250)
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 p.screenshot(path=str(SCREENSHOTS/'final-approval-checklist-mobile.png'))
 p.locator('[data-founder-group] label.pill',has_text='Leave Open').click()
 expect(p.get_by_role('button',name='Continue',exact=True)).to_be_enabled()
 assert not state['posts']


def test_child_cost_pending_is_not_zero_or_legacy_corruption(page):
 p,base,root=page;fake(p)
 totals={'price':None,'price_usd':None,'evidence':{'scope':'child ledger','complete':False,'provider_pending':True}}
 p.route('**/api/tasks/concept/cost',lambda r:r.fulfill(json=totals))
 p.goto(base+'/tasks/concept/decisions/wait-concept');host=p.locator('#sessioncost')
 expect(host).to_have_text('Session Cost Pending')
 assert 'final total is not known' in host.get_attribute('title')
 totals['price']=.73;totals['price_usd']=.73
 _return_to_tab_until(p,host,'0.73 credits · Pending')
 p.screenshot(path='/tmp/requi-test-artifacts/child-cost-pending.png',full_page=True)
 totals['evidence']['complete']=True
 _return_to_tab_until(p,host,'0.73 credits')


def _applying_semantic_page(p,base):
 control=review();control['kind']='semantic_review';control['options']=[]
 control['decision']={'options':[],'context':{'decision_purpose':'semantic_review','evidence':{'semantic_review':{'revision':'a'*64,'findings':[{'finding_id':'f1','item_id':'FD-1','path':['constraints',0],'reason':'Repeats a need','kind':'semantic','original':'Fast app'}]}}}}
 held=[]
 def route(r):
  if r.request.method=='POST':held.append(r);return
  r.fulfill(json={'task_id':'concept','title':'Habit tracking','review':control,'current':control,'actionable':True,'draft':None})
 p.route('**/api/tasks/concept**',route)
 p.add_init_script("window.EventSource=class{constructor(){this.l={}}addEventListener(){}close(){}}")
 p.set_viewport_size({'width':1404,'height':819})
 p.goto(base+'/tasks/concept/decisions/wait-concept?step=findings');expect(p.locator('h1')).to_have_text('Review Flagged Inputs')
 p.locator('input[name="action-0"][value="accept"]').check()
 p.get_by_role('button',name='Apply decisions and continue').click()
 expect(p.locator('#semanticstatus details.review-progress')).to_be_visible()


def test_applying_decisions_shows_one_plain_progress_block_without_a_gap_or_duplicate_banner(page):
 p,base,root=page;_applying_semantic_page(p,base)
 p.screenshot(path='/tmp/applying.png',full_page=True)
 summary=p.locator('#semanticstatus details.review-progress > summary').inner_text()
 assert 'resubmitting' not in summary and 'engine signal' not in summary and 'Connected to progress' not in summary,summary
 # The 'Before you can continue' checklist must not repeat the applying state.
 checklist=p.locator('#decision-checklist')
 assert checklist.count()==0 or not checklist.is_visible()
 # No dead space between the progress block and the buttons.
 box=p.locator('#semanticstatus details.review-progress').bounding_box();button=p.get_by_role('button',name='Save draft',exact=True).bounding_box()
 assert button['y']-(box['y']+box['height'])<24,(box,button)


def test_sticky_header_never_covers_the_field_being_edited(page):
 p,base,root=page;_applying_semantic_page(p,base)
 head=p.locator('.semantic-head').bounding_box()
 assert head['height']<=90 or p.locator('.semantic-head').evaluate("e=>getComputedStyle(e).position")!='sticky',head
