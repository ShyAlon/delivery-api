import uuid
import pytest
from test_postgres_store import store,A,B,DSN
from test_attempt_ledger import ledger
from delivery_api.tenancy import tenant_context,TenantAccessDenied
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.operator_reads import OperatorReadStore
pytestmark=pytest.mark.skipif(not DSN,reason='disposable PG required')


def test_explicit_read_registry_cross_owner_without_backend_policy_leak(store):
 import psycopg
 ids=[];sessions=[]
 for scope in (A,B):
  with tenant_context(scope):
   tid=store.create_task('operator-read-'+uuid.uuid4().hex)['task_id'];sid='op-'+uuid.uuid4().hex
   store.set_engine_session(tid,sid);ids.append((scope,tid));sessions.append(sid)
 principal=OperatorPrincipal(A.owner_id,'fake-v1',frozenset({'sessions.read'}))
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_read TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'fake-v1',['sessions.read']))
 try:
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),principal)
  with tenant_context(A):
   got=reader.sessions(store);assert set(sessions)<={row['session_id'] for row in got['sessions']}
   assert not got['mutations_available'] and store.session_binding(sessions[1]) is None
   with store._postgres.transaction() as c:
    c.execute('SET LOCAL ROLE requi_operator_read')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute("UPDATE public.tasks SET title='bad' WHERE task_id=%s",(ids[1][1],))
  with tenant_context(B):
   with pytest.raises(TenantAccessDenied):reader.sessions(store)
  with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
  with tenant_context(A):
   with pytest.raises(TenantAccessDenied):reader.sessions(store)
 finally:
  with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
  for scope,tid in ids:
   with tenant_context(scope):store.delete_task(tid)


def test_operator_credit_pages_preserve_negative_fixed_point_and_capability(store):
 import psycopg
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_read TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'fake-v1',['analytics.read']))
  for owner,balance in ((A.owner_id,-17),(B.owner_id,1000)):
   c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES (%s,%s,100,0)',(owner,balance))
 try:
  principal=OperatorPrincipal(A.owner_id,'fake-v1',frozenset({'analytics.read'}))
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),principal)
  with tenant_context(A):
   first=reader.credits(store,limit=1)
   assert first['truncated'] and first['accounts']==[{'owner_id':A.owner_id,'balance_units':-17,'units_per_credit':100,'updated_at':0}]
   second=reader.credits(store,limit=1,after_owner=first['next_owner'])
   assert not second['truncated'] and second['accounts'][0]['owner_id']==B.owner_id
   assert not second['credit_adjustment_available']
   with pytest.raises(TenantAccessDenied):reader.sessions(store)
   with pytest.raises(ValueError):reader.credits(store,after_owner='bad')
  with tenant_context(B):
   with pytest.raises(TenantAccessDenied):reader.credits(store)
 finally:
  with psycopg.connect(DSN) as c:
   c.execute('DELETE FROM credit_accounts WHERE owner_id=ANY(%s::uuid[])',([A.owner_id,B.owner_id],))
   c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))


def test_operator_expense_aggregate_uses_settlement_time_and_retains_old_holds(ledger):
 import psycopg,time
 from test_attempt_ledger import Q
 from delivery_api.attempt_ledger import _quote
 store,tid,job=ledger
 owner=A;now=time.time();expense,retail=_quote(Q)
 # This aggregate fixture deliberately retains three simultaneous historical
 # attempts; configure its TEST ONLY concurrency bound explicitly.
 with psycopg.connect(DSN) as c:c.execute('UPDATE admission_limits SET concurrency_max=3 WHERE owner_id=%s',(A.owner_id,))
 with tenant_context(owner):
  ids=[uuid.uuid4().hex for _ in range(3)]
  for aid in ids:store.reserve_provider_attempt(job,aid,Q,'fake')
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_read TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'fake-v1',['analytics.read']))
  c.execute("UPDATE provider_attempts SET state='settled',expense_actual=7,retail_actual=0,settled_at=%s WHERE attempt_id=%s",(now,ids[0]))
  c.execute("UPDATE provider_attempts SET state='settled',expense_actual=99,retail_actual=0,settled_at=%s WHERE attempt_id=%s",(now-90000,ids[1]))
  c.execute("UPDATE provider_attempts SET state='uncertain',created_at=%s WHERE attempt_id=%s",(now-90000,ids[2]))
 try:
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'fake-v1',frozenset({'analytics.read'})))
  with tenant_context(A):
   got=reader.expense_totals(store)
   assert got['rolling_settled_units']==7 and got['outstanding_hold_units']==expense
   assert got['conservative_used_units']==7+expense and got['uncertain_attempts']==1
   assert not got['platform_limit_available'] and not got['installed_in_admission']
  with psycopg.connect(DSN) as c:c.execute('UPDATE provider_attempts SET settled_at=NULL WHERE attempt_id=%s',(ids[0],))
  with tenant_context(A):
   with pytest.raises(TenantAccessDenied,match='incomplete'):reader.expense_totals(store)
 finally:
  with psycopg.connect(DSN) as c:
   c.execute('DELETE FROM provider_attempts WHERE attempt_id=ANY(%s)',(ids,))
   c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
  # ledger fixture owns cleanup after the aggregate evidence is removed.


def test_operator_debug_metadata_has_separate_capability_and_no_content_grants(store):
 import psycopg
 with tenant_context(B):
  tid=store.create_task('secret brief '+uuid.uuid4().hex)['task_id']
  store.set_engine_session(tid,'debug-'+uuid.uuid4().hex)
  store.enqueue_worker(tid,'fake-phase')
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_read TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'fake-debug',['debug.read']))
 try:
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'fake-debug',frozenset({'debug.read'})))
  with tenant_context(A):
   got=reader.task_debug(store,tid)
   assert got['task']['owner_id']==B.owner_id and len(got['worker_jobs'])==1
   assert got['content']=='state_metadata_only' and not got['logs_available'] and not got['quiescence_verified']
   assert set(got['task'])=={'task_id','owner_id','state','active_phase','generation','created_at','updated_at','deleted_at'}
   assert 'brief' not in str(got) and 'execution_options' not in str(got)
   with pytest.raises(TenantAccessDenied):reader.sessions(store)
   with pytest.raises(KeyError):reader.task_debug(store,'missing')
   with pytest.raises(ValueError):reader.task_debug(store,'x'*241)
   for query in ('SELECT brief FROM public.tasks','SELECT execution_options FROM public.worker_jobs'):
    with store._postgres.transaction() as c:
     c.execute('SET LOCAL ROLE requi_operator_read')
     with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(query)
  with tenant_context(B):
   with pytest.raises(TenantAccessDenied):reader.task_debug(store,tid)
  with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
  with tenant_context(A):
   with pytest.raises(TenantAccessDenied):reader.task_debug(store,tid)
 finally:
  with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
  with tenant_context(B):store.delete_task(tid)


def test_curated_state_events_are_append_only_capability_separate_and_paged(store):
 import psycopg
 with tenant_context(B):
  tid=store.create_task('secret-log-title-'+uuid.uuid4().hex)['task_id']
  store.request_task_stop(tid)
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_read TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(A.owner_id,'logs-fake',['logs.read']))
 try:
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(A.owner_id,'logs-fake',frozenset({'logs.read'})))
  with tenant_context(A):
   got=reader.task_logs(store,tid,limit=1)
   assert got['truncated'] and got['events'][0]['state']=='stop_requested'
   older=reader.task_logs(store,tid,limit=1,before_id=int(got['next_before_id']))
   assert not older['truncated'] and older['events'][0]['event']=='created'
   assert 'secret-log-title' not in str(got) and got['raw_logs_available'] is False
   assert got['historical_backfill'] is False and got['quiescence_verified'] is False
   with pytest.raises(TenantAccessDenied):reader.task_debug(store,tid)
   for cursor in (0,-1,True,9223372036854775808):
    with pytest.raises(ValueError):reader.task_logs(store,tid,before_id=cursor)
   with store._postgres.transaction() as c:
    c.execute('SET LOCAL ROLE requi_operator_read')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute("DELETE FROM operator_state_events WHERE task_id=%s",(tid,))
  with tenant_context(B):
   with store._postgres.transaction() as c:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM operator_state_events')
   with pytest.raises(TenantAccessDenied):reader.task_logs(store,tid)
  with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(A.owner_id,))
  with tenant_context(A):
   with pytest.raises(TenantAccessDenied):reader.task_logs(store,tid)
 finally:
  with psycopg.connect(DSN) as c:c.execute('DELETE FROM operator_principals WHERE subject=%s',(A.owner_id,))
  with tenant_context(B):
   # No engine/worker was ever created in this fixture. Confirm that fact via
   # the existing stop path, rather than deleting while Stop is pending.
   store.confirm_task_stopped(tid,engine_quiescent=True)
   store.delete_task(tid)


def test_task_usage_separate_role_exact_tokens_and_actual_charges(ledger):
 import psycopg
 from test_attempt_ledger import Q
 store,tid,job=ledger;aid=uuid.uuid4().hex
 with tenant_context(A):
  store.reserve_provider_attempt(job,aid,Q,'usage-test');store.transition_provider_attempt(aid,'start')
  store.transition_provider_attempt(aid,'settle',{'input_tokens':2,'output_tokens':3})
 with psycopg.connect(DSN) as c:
  c.execute('GRANT requi_operator_usage TO delivery_app')
  c.execute('INSERT INTO operator_principals VALUES (%s,%s,%s,true)',(B.owner_id,'usage-fake',['usage.read']))
  c.execute("INSERT INTO credit_charges(attempt_id,owner_id,state,charged_units,created_at,settled_at,units_per_credit) VALUES (%s,%s,'settled',18,0,1,1000)",(aid,A.owner_id))
 try:
  reader=OperatorReadStore(DSN.replace('user=sandbox','user=delivery_app'),OperatorPrincipal(B.owner_id,'usage-fake',frozenset({'usage.read'})))
  with tenant_context(B):
   got=reader.task_usage(store,tid)
   assert got['total_tokens']=='5' and got['input_tokens']=='2' and got['output_tokens']=='3'
   assert got['settled_credit_groups']==[{'charged_units':'18','units_per_credit':'1000','charges':1}] and got['complete']
   with pytest.raises(TenantAccessDenied):reader.sessions(store)
   for query in ('SELECT raw_usage FROM provider_attempts','SELECT quote FROM provider_attempts','UPDATE credit_charges SET charged_units=0'):
    with store._postgres.transaction() as c:
     c.execute('SET LOCAL ROLE requi_operator_usage')
     with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(query)
  with psycopg.connect(DSN) as c:c.execute('UPDATE credit_charges SET units_per_credit=NULL WHERE attempt_id=%s',(aid,))
  with tenant_context(B):
   got=reader.task_usage(store,tid);assert got['unknown_charge_scale_or_amount']==1 and not got['complete'] and got['settled_credit_groups']==[]
  with psycopg.connect(DSN) as c:c.execute("UPDATE provider_attempts SET usage='{}' WHERE attempt_id=%s",(aid,))
  with tenant_context(B):
   got=reader.task_usage(store,tid);assert got['unknown_settled_usage']==1 and not got['complete']
  with tenant_context(A):
   with pytest.raises(TenantAccessDenied):reader.task_usage(store,tid)
  with psycopg.connect(DSN) as c:c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s',(B.owner_id,))
  with tenant_context(B):
   with pytest.raises(TenantAccessDenied):reader.task_usage(store,tid)
 finally:
  with psycopg.connect(DSN) as c:
   c.execute('DELETE FROM credit_charges WHERE attempt_id=%s',(aid,));c.execute('DELETE FROM operator_principals WHERE subject=%s',(B.owner_id,))


def test_future_credit_adjustment_evidence_is_private_consistent_and_unique(store):
 import psycopg
 adjustment=uuid.uuid4();key='test-adjustment-'+uuid.uuid4().hex
 values=(adjustment,key,A.owner_id,B.owner_id,'fake-v1','a'*64,'fake-owner-review','test',10,1000,-3,7,1,2)
 query='INSERT INTO credit_adjustments VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
 with psycopg.connect(DSN) as c:c.execute(query,values)
 try:
  for scope in (A,B):
   with tenant_context(scope):
    for role in ('requi_backend','requi_operator_read','requi_operator_usage'):
     with store._postgres.transaction() as c:
      c.execute('SET LOCAL ROLE '+role)
      with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM credit_adjustments')
  with psycopg.connect(DSN) as c:
   with pytest.raises(psycopg.errors.UniqueViolation):c.execute(query,(uuid.uuid4(),*values[1:]))
  with psycopg.connect(DSN) as c:
   with pytest.raises(psycopg.errors.CheckViolation):c.execute(query,(uuid.uuid4(),key+'bad',*values[2:11],8,*values[12:]))
  # Merely recording evidence never changes the account balance.
  with psycopg.connect(DSN) as c:assert c.execute('SELECT count(*) FROM credit_accounts WHERE owner_id=%s',(A.owner_id,)).fetchone()[0]==0
 finally:
  with psycopg.connect(DSN) as c:c.execute('DELETE FROM credit_adjustments WHERE adjustment_id=%s',(adjustment,))
