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
