"""Installed-wheel web + back-office HTTP/Chromium check. Fake identity/data only."""
import os,json,socket,threading,time
from pathlib import Path
for name in ('OPENAI_API_KEY','DEEPSEEK_API_KEY','SUPABASE_SERVICE_ROLE_KEY','DELIVERY_POSTGRES_DSN'):
    assert not os.environ.get(name),'test must receive no live credentials'
assert not os.environ.get('PYTHONPATH'),'installed wheels only'
# Fixture-only import construction, before app import. No network/database I/O.
os.environ['DELIVERY_DATABASE_URL']='postgresql://fixture.invalid/isolated-test'
os.environ['DELIVERY_SUPABASE_URL']='https://isolated-smoke.supabase.co'
os.environ['DELIVERY_SUPABASE_ANON_KEY']='hermetic-public-key'
import delivery_api.store as _store_module
_original_factory=_store_module.create_store
_store_module.create_store=lambda dsn:__import__('types').SimpleNamespace(auth_session_active=lambda:False)
try:
 from delivery_api import app as service,config
finally:_store_module.create_store=_original_factory
from delivery_api.backoffice import create_app
from delivery_api.tenant_http import TenantMiddleware
from delivery_api.tenancy import TenantScope,current_scope,TenantAccessDenied
from playwright.sync_api import sync_playwright
import uvicorn
assert 'site-packages' in __import__('delivery_api').__file__
OWNER='00000000-0000-0000-0000-000000000001';SID='00000000-0000-0000-0000-000000000011'
FAKE_TOKEN='eyJhbGciOiJub25lIn0.eyJleHAiOjQxMDI0NDQ4MDB9.fake-ui-only'
active=[True];base={'scope':'reviewed_cross_user_operator_read'}
session={'task_id':'fake-task','session_id':'fake-session','owner_id':OWNER,'generation':2,'created_at':123}
account={'owner_id':OWNER,'balance_units':125000,'units_per_credit':1000,'updated_at':123}
class Verifier:
 def verify(self,token):
  if token!=FAKE_TOKEN:raise TenantAccessDenied('fake test identity only')
  return TenantScope(OWNER,SID)
class Store:
 def auth_session_active(self):return active[0] and current_scope().owner_id==OWNER
 def operator_workspace_snapshot(self):return {'scope':'current_authorized_workspace','credits':account,'sessions':[session],'worker_jobs':[]}
 def list_tasks(self):return [{'task_id':'fake-task','title':'Fake preview task','brief':'Read-only fixture. No provider calls.','state':'draft','phases':[],'gates':[],'created_at':123,'updated_at':123,'artifact_count':0}]
class Reader:
 def guard(self):
  if not active[0] or current_scope().owner_id!=OWNER:raise TenantAccessDenied('fake revoked')
 def sessions(self,store,**kwargs):self.guard();return base|{'sessions':[session],'truncated':False}
 def credits(self,store,**kwargs):self.guard();return base|{'accounts':[account],'truncated':False}
 def credit_account(self,store,owner_id):self.guard();assert owner_id==OWNER;return base|{'account':account}
 def expense_totals(self,store):self.guard();return base|{'as_of':123,'rolling_settled_units':1,'outstanding_hold_units':2,'conservative_used_units':3,'uncertain_attempts':0}
 def task_debug(self,store,task_id):self.guard();assert task_id=='fake-task';return base|{'content':'state_metadata_only','task':session|{'state':'stop_requested'},'worker_jobs':[],'worker_jobs_truncated':False}
 def task_logs(self,store,task_id,**kwargs):self.guard();assert task_id=='fake-task';return base|{'content':'curated_state_events_only','raw_logs_available':False,'historical_backfill':False,'events':[{'id':'12','owner_id':OWNER,'task_id':'fake-task','source':'task','event':'state_changed','state':'stop_requested','generation':2,'ts':123}],'truncated':False,'next_before_id':None}
 def task_usage(self,store,task_id):self.guard();assert task_id=='fake-task';return base|{'input_tokens':'2','output_tokens':'3','total_tokens':'5','settled_credit_groups':[{'charged_units':'18','units_per_credit':'1000'}],'complete':False,'unresolved_attempts':1,'unknown_settled_usage':0,'pending_charges':1,'unknown_charge_scale_or_amount':0,'settled_attempts_without_charge':0}
# Trusted test fixture injection only. Production entry point never installs it.
config.CLOUD_TENANTS=True;service.store=Store();service.app.state.store=service.store
service.app.state.operator_read_store=Reader()
wrapped=TenantMiddleware(service.app,verifier=Verifier(),admin_subjects=[OWNER],session_check=service.store.auth_session_active)
def free_port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
a,b=free_port(),free_port();servers=[];threads=[];seen=[]
try:
 for app,port in ((wrapped,a),(create_app(service_url=f'http://127.0.0.1:{a}'),b)):
  server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'));servers.append(server)
  thread=threading.Thread(target=server.run);thread.start();threads.append(thread)
  for _ in range(200):
   if server.started:break
   time.sleep(.02)
  assert server.started
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1100,'height':1000},accept_downloads=True)
  page=context.new_page();blocked=[]
  def isolate(route):
   if route.request.url.startswith((f'http://127.0.0.1:{a}/',f'http://127.0.0.1:{b}/')):route.continue_()
   else:blocked.append(route.request.url);route.abort()
  page.route('**/*',isolate);page.on('request',lambda request:seen.append(request.url))
  page.goto(f'http://127.0.0.1:{a}/');assert 'Requi' in page.title()
  context.add_cookies([{'name':'requi_access','value':FAKE_TOKEN,'url':f'http://127.0.0.1:{a}','httpOnly':True,'sameSite':'Lax'}])
  page.goto(f'http://127.0.0.1:{a}/');page.wait_for_selector('.task-grid .task');assert 'Fake preview task' in page.locator('.task-grid').inner_text()
  page.screenshot(path='/tmp/ui-web.png',full_page=True)
  page.goto(f'http://127.0.0.1:{b}/');page.wait_for_function("() => document.getElementById('global-sessions').hidden===false")
  assert not any('task-debug' in url for url in seen)
  assert page.locator('#balance').inner_text()=='125 credits'
  page.click('#accounts button.expand');page.wait_for_function("() => document.querySelector('#accounts .detail').textContent.includes('125 credits')")
  page.click('#global-sessions button.expand');page.wait_for_function("() => document.querySelector('#global-sessions .detail').textContent.includes('0.018 credits')")
  assert 'Settled total tokens' in page.locator('#global-sessions .detail').inner_text()
  with page.expect_download() as pending:page.locator('#global-sessions .detail button').filter(has_text='Download').click()
  assert json.loads(Path(pending.value.path()).read_text())['events'][0]['state']=='stop_requested'
  page.screenshot(path='/tmp/ui-desktop.png',full_page=True)
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/ui-mobile.png',full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.fill('#accounts-filter','no-match');assert page.locator('#accounts').is_hidden()
  active[0]=False;page.reload();page.wait_for_function("() => document.getElementById('status').textContent==='Sign in to the service first.'")
  assert page.locator('#global-sessions').is_hidden()
  assert all(url.startswith((f'http://127.0.0.1:{a}/',f'http://127.0.0.1:{b}/')) or url in blocked for url in seen)
  browser.close()
 print(json.dumps({'result':'passed','web_listener_reachable':True,'backoffice_listener_reachable':True,'auto_lists_and_fresh_details':True,'actual_json_download':True,'task_tokens_and_credits':True,'desktop_mobile_checked':True,'revocation_checked':True,'fixture':'fake identity and service data, not production RLS','live_provider_calls':0,'migrations_applied':False}))
finally:
 for server in servers:server.should_exit=True
 for thread in threads:thread.join(timeout=10);assert not thread.is_alive()
