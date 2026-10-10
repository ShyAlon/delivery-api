"""Actual isolated PG +signed synthetic auth +stub PayPal; not real signup/network."""
import importlib.util,sys,uuid,time,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest,jwt
from cryptography.hazmat.primitives.asymmetric import ec
from test_item6_isolation import config
from delivery_api.tenancy import tenant_context
from delivery_api.supabase_auth import SupabaseIdentityVerifier
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.postgres_store import PostgresStore
from delivery_api.payment_repository import PaymentRepository
from delivery_api.paypal_adapter import PayPalAdapter,PayPalBinding
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('item6_private',ROOT/'scripts/item6/private_adapters.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ADMIN=os.getenv('ITEM6_TEST_ADMIN_DSN');pytestmark=pytest.mark.skipif(not ADMIN,reason='explicit isolated item6 PostgreSQL required')


def test_fresh_verified_identity_operator_sandbox_balance_task_chain(tmp_path):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 info=conninfo_to_dict(ADMIN);assert info['dbname'].startswith('requi_item6_') and info['host']=='/tmp'
 tenant=make_conninfo(**(info|{'user':'delivery_app'}));payment=make_conninfo(**(info|{'user':'delivery_payment_test'}))
 owner=str(uuid.uuid4());session=str(uuid.uuid4());key=ec.generate_private_key(ec.SECP256R1())
 with psycopg.connect(ADMIN) as c:
  c.execute('INSERT INTO auth.users(id) VALUES(%s)',(owner,));c.execute('INSERT INTO auth.sessions(id,user_id) VALUES(%s,%s)',(session,owner))
  c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000,0)',(owner,))
  c.execute('UPDATE operator_principals SET enabled=false WHERE enabled')
  c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'item6-hermetic',ARRAY['payments.read'],true)",(owner,))
 store=PostgresStore(tenant);cfg=__import__('dataclasses').replace(config(tmp_path),run_id=uuid.uuid4().hex)
 token=jwt.encode({'sub':owner,'session_id':session,'role':'authenticated','iat':int(time.time()),'exp':int(time.time())+300,'aud':'authenticated','iss':'https://fixture.supabase.co/auth/v1'},key,algorithm='ES256')
 verifier=SupabaseIdentityVerifier('https://fixture.supabase.co',key_provider=SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=key.public_key())))
 identity=m.IdentityAdapter(signup=lambda:{'token':token,'fresh_signup':True,'source_mode':'hermetic'},verifier=verifier,store=store)
 receipt=identity(cfg,{})
 assert receipt['subject']==owner and 'token' not in receipt
 principal=OperatorPrincipal(owner,'item6-hermetic',frozenset({'payments.read'}))
 operator=m.OperatorAdapter(identity,store=store,read_reviewed_registry=lambda:principal)(cfg,{'identity':receipt})
 purchased=hermetic_purchase(cfg,identity,store,ADMIN,receipt,operator)
 with tenant_context(identity.scope):
  task=store.create_task('item6-'+cfg.run_id);store.set_engine_session(task['task_id'],'item6-session-'+cfg.run_id)
  assert task['owner_id']==purchased['subject']
 with psycopg.connect(ADMIN) as c:c.execute('DELETE FROM auth.sessions WHERE id=%s',(session,))
 with tenant_context(identity.scope):
  assert not store.auth_session_active() and store.task(task['task_id']) is None


def hermetic_purchase(cfg,identity,store,ADMIN,receipt,operator):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 info=conninfo_to_dict(ADMIN);payment=make_conninfo(**(info|{'user':'delivery_payment_test'}))
 binding=PayPalBinding('fixture-item6-merchant','fixture-item6-webhook',False);repo=PaymentRepository(payment,binding.ledger_binding)
 class ProcessorStub:
  def __init__(self):self.binding=binding;self.payload=None;self.state='CREATED';self.oid='order-'+cfg.run_id;self.cid='capture-'+cfg.run_id
  def order(self):
   u=dict(self.payload['purchase_units'][0]);u['payments']={'captures':[self.capture()]}
   return {'id':self.oid,'intent':'CAPTURE','status':self.state,'purchase_units':[u],'links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token='+self.oid}]}
  def capture(self):return {'id':self.cid,'status':'COMPLETED','final_capture':True,'amount':{'currency_code':'USD','value':'5.00'},'supplementary_data':{'related_ids':{'order_id':self.oid}}}
  def request(self,method,path,payload=None,headers=None):
   if path=='/v1/notifications/verify-webhook-signature':return {'verification_status':'SUCCESS'}
   if path=='/v2/checkout/orders' and method=='POST':self.payload=payload;return self.order()
   if path.endswith('/capture'):self.state='COMPLETED';return self.order()
   if path.startswith('/v2/payments/captures/'):return self.capture()
   return self.order()
 stub=ProcessorStub();adapter=PayPalAdapter(stub,binding)
 def balance(scope):
  with psycopg.connect(ADMIN) as c:return c.execute('select balance_units from credit_accounts where owner_id=%s',(scope.owner_id,)).fetchone()[0]
 def approve(value):assert value['approval_url'].startswith('https://www.sandbox.paypal.com/');stub.state='APPROVED'
 def webhook():
  from test_paypal_adapter import HEADERS
  return json.dumps({'id':'event-'+cfg.run_id,'event_type':'PAYMENT.CAPTURE.COMPLETED','resource':stub.capture()}).encode(),HEADERS
 purchased=m.SandboxPurchaseAdapter(identity,repository=repo,processor=adapter,approve=approve,webhook_receipt=webhook,read_balance=balance)(cfg,{'identity':receipt,'operator':operator})
 assert purchased['balance_units']==5000 and purchased['posted_count']==1
 return purchased
