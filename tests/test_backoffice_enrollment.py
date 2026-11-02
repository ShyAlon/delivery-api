import importlib.util,io,json,os
from pathlib import Path
import psycopg
from uuid import uuid4
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('enroll',ROOT/'scripts/owner/backoffice-access.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_enrollment_is_dry_confirmed_scoped_and_preserves(tmp_path):
 dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN'];subject=str(uuid4());caps=sorted(m.setup.check.DEBUG_CAPABILITIES|{'credits.allocate'})
 f=tmp_path/'private.env';f.write_text('');f.chmod(0o600)
 from dotenv import set_key
 values={'DELIVERY_DATABASE_URL':dsn.replace('user=sandbox','user=delivery_app'),'REQUI_ADMIN_DATABASE_URL':dsn,'REQUI_OPERATOR_SUBJECT':subject,'REQUI_OPERATOR_REGISTRY_VERSION':'ops-v1','REQUI_OPERATOR_CAPABILITIES':json.dumps(caps)}
 for k,v in values.items():set_key(f,k,v)
 with psycopg.connect(dsn) as c:
  c.execute('DELETE FROM operator_principals');c.execute('INSERT INTO auth.users(id) VALUES(%s)',(subject,));c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(subject,'ops-v1',caps))
 try:
  before=f.read_bytes();out=io.StringIO()
  assert m.run(ROOT,explicit=f,out=out)==0 and f.read_bytes()==before
  assert m.run(ROOT,explicit=f,apply=True,confirm=lambda _: 'no',out=out)==1
  with psycopg.connect(dsn) as c:assert c.execute('SELECT capabilities FROM operator_principals').fetchone()[0]==caps
  def confirm(prompt):
   for cap,purpose in m.PURPOSES.items():assert cap in prompt and purpose in prompt
   return 'ENROLL REVIEWED BACK OFFICE ACCESS'
  assert m.run(ROOT,explicit=f,apply=True,confirm=confirm,out=out)==0
  with psycopg.connect(dsn) as c:updated=set(c.execute('SELECT capabilities FROM operator_principals').fetchone()[0])
  assert updated==set(caps)|m.DEFAULT_CAPABILITIES
  saved=m.setup.settings_module().read_file(f)
  assert set(json.loads(saved['REQUI_OPERATOR_CAPABILITIES']))==updated
  assert saved['DELIVERY_DATABASE_URL']==values['DELIVERY_DATABASE_URL']
  assert 'users.suspend' not in updated and 'sessions.operate' not in updated
  assert all(line.rsplit('=',1)[1] in ('true','false') for line in out.getvalue().splitlines())
  assert subject not in out.getvalue() and dsn not in out.getvalue()
 finally:
  with psycopg.connect(dsn) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(subject,));c.execute('DELETE FROM public.users WHERE user_id=%s',(subject,));c.execute('DELETE FROM auth.users WHERE id=%s',(subject,))


def test_read_setup_preserves_existing_reviewed_effect_but_does_not_copy_new(tmp_path,monkeypatch):
 from contextlib import contextmanager
 subject=str(uuid4());caps=m.setup.check.DEBUG_CAPABILITIES|{'services.operate','credits.allocate'}
 values={'DELIVERY_DATABASE_URL':'postgresql://app@host/db','REQUI_ADMIN_DATABASE_URL':'postgresql://admin@host/db','REQUI_OPERATOR_SUBJECT':subject,'REQUI_OPERATOR_REGISTRY_VERSION':'ops-v1','REQUI_OPERATOR_CAPABILITIES':json.dumps(sorted(m.setup.check.DEBUG_CAPABILITIES|{'services.operate'}))}
 settings=m.setup.settings_module();f=tmp_path/'private.env';f.write_text('');f.chmod(0o600)
 from dotenv import set_key
 for k,v in values.items():set_key(f,k,v)
 class Result:
  def fetchall(self):return [(sorted(caps),)]
 class Connection:
  def execute(self,*args):return Result()
 @contextmanager
 def connect(_):yield Connection()
 monkeypatch.setattr(m.setup,'read_registry',lambda _:dict(zip(m.setup.check.SETTINGS,(subject,'ops-v1',json.dumps(sorted(m.setup.check.DEBUG_CAPABILITIES))))))
 assert m.setup.run(ROOT,explicit=f,connect=connect,confirm=lambda _: 'yes',out=io.StringIO())==0
 saved=settings.read_file(f)
 actual=set(json.loads(saved['REQUI_OPERATOR_CAPABILITIES']))
 assert 'services.operate' in actual and 'credits.allocate' not in actual


def test_effective_access_requires_registry_and_config_and_active_subject(pg_workspace_factory):
 from delivery_api.backoffice_access import effective
 from delivery_api.operator_reads import OperatorReadStore
 from delivery_api.operator_authorization import OperatorPrincipal
 from delivery_api.tenancy import tenant_context
 with pg_workspace_factory() as w,tenant_context(w.scope):
  with w.admin() as c:
   c.execute('DELETE FROM operator_principals');c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(w.scope.owner_id,'test-access',sorted(m.DEFAULT_CAPABILITIES)))
  reader=OperatorReadStore(w.dsn.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(w.scope.owner_id,'test-access',m.DEFAULT_CAPABILITIES))
  try:
   assert all(effective(reader,w.store)['capabilities'].values())
   with w.admin() as c:c.execute("UPDATE operator_principals SET capabilities=array_remove(capabilities,'services.operate')")
   denied=effective(reader,w.store)['capabilities']
   assert denied['services.operate'] is False and denied['logs.read'] is True
   with w.admin() as c:c.execute('UPDATE operator_principals SET enabled=false')
   assert not any(effective(reader,w.store)['capabilities'].values())
  finally:
   reader.db.close()
   with w.admin() as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(w.scope.owner_id,))
