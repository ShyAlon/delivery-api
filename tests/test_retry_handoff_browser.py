"""The actual retry dialogs release on a new command while POST remains pending."""
from urllib.parse import urlparse
from playwright.sync_api import expect
import pytest

@pytest.mark.parametrize('phase,endpoint,needs',[('architecture','retry-phase',False),('needs','retry-needs',True)])
def test_actual_retry_dialog_handoff_guard_and_late_failure(pg_browser,phase,endpoint,needs):
 page,base,_=pg_browser;state={'confirmed':[],'pending':None,'reads':0,'command':False}
 def route(r):
  path=urlparse(r.request.url).path
  if path=='/api/model-routes':r.fulfill(json={'routes':[{'id':'deepseek','models':['fixture'],'available':True}]});return
  if path=='/api/tasks/retry-fixture/'+endpoint:
   body=r.request.post_data_json
   if body['confirm']:state['confirmed'].append(body);state['pending']=r;return
   r.fulfill(json={'phase':phase,'label':phase.title(),'default_model':'deepseek:fixture','last_failure':'Fixture failure','message':'Fixture retry','checkpoint':True});return
  if path=='/api/tasks/retry-fixture':
   state['reads']+=1;r.fulfill(json={'state':'running','events':[{'id':10,'phase_key':phase,'kind':'command'}]+([{'id':11,'phase_key':phase,'kind':'command'}] if state['command'] else [])});return
  r.continue_()
 page.route('**/api/**',route);page.goto(base);page.get_by_role('button',name='+ New task').wait_for()
 page.evaluate('''async ({phase,needs})=>{const {createTaskActions}=await import('/assets/taskactions.js');const {Dialog}=await import('/assets/shared-dialog.js');
  document.querySelector('#app').innerHTML='<button id="retryphase">Retry phase</button><button id="retryneeds">Retry needs</button>';
  const modal=document.querySelector('#modal');window.detailCount=0;window.lateErrors=[];
  window.retryActions=createTaskActions({closeModal:()=>Dialog.close(modal),openDialog:(html,options)=>Dialog.open(modal,html,options),detail:async()=>{window.detailCount++},showError:e=>{window.lateErrors.push(e.message)},bindDialogKeys:()=>{}});
  window.fixtureTask={task_id:'retry-fixture',events:[{id:10,kind:'command',phase_key:phase}]};
  window.openRetry=()=>needs?retryActions.showFailedNeedsRetry(fixtureTask):retryActions.showFailedPhaseRetry(fixtureTask,{phase_key:phase,label:phase});await openRetry();
 }''',{'phase':phase,'needs':needs})
 dialog=page.get_by_role('dialog');expect(dialog).to_be_visible();dialog.locator('#confirmretry').click()
 page.wait_for_function("document.querySelector('#confirmretry').disabled")
 # A stale task.running or old command does not dismiss it.
 page.wait_for_timeout(2300);expect(dialog).to_be_visible();assert state['reads']>=2
 state['command']=True
 expect(dialog).to_have_count(0,timeout=7000);assert page.evaluate('detailCount')==1
 assert state['pending'] is not None and len(state['confirmed'])==1
 # Reopening the retry UI while the POST is pending cannot dispatch a second request.
 page.evaluate('openRetry()');page.locator('#confirmretry').click();assert len(state['confirmed'])==1
 # Simulate the late request failure after authoritative launch. It remains visible.
 page.evaluate("history.replaceState({},'', '/tasks/retry-fixture/view')")
 state['pending'].fulfill(status=500,json={'detail':'Fixture late failure'})
 page.wait_for_function('lateErrors.length===1');assert 'Check current task progress before retrying' in page.evaluate('lateErrors[0]')
 page.screenshot(path=f'/tmp/retry-handoff-{phase}.png')
