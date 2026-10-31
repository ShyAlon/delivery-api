"""Actual production factory -> atomic child broker -> disposable PG guards."""
import base64,json,time,uuid
import httpx,pytest
from delivery_api.tenancy import tenant_context
from delivery_api import production_gateway as pg
from delivery_api.child_provider_broker import ChildProviderBroker,BrokerPaused
from delivery_api.child_broker_journal import ChildBrokerJournal
from delivery_api.child_execution_contract import decode_operation
from test_child_execution_contract import operation,frame
from test_production_gateway import env,MODEL
from test_attempt_ledger import credit_account,A,DSN,LAUNCH_Q,LAUNCH_USAGE
S=10**12


@pytest.mark.parametrize('guard',['allowed','own_allowed','missing','own_test','session','platform','legacy_cap'])
def test_production_child_dispatch_obeys_composed_guards(credit_account,guard):
    import psycopg
    store,tid,job=credit_account;calls=[]
    with psycopg.connect(DSN) as c:
        c.execute('UPDATE credit_accounts SET balance_units=1000000000000,units_per_credit=1000000000 WHERE owner_id=%s',(A.owner_id,))
        if guard!='missing':c.execute("INSERT INTO expense_guard_policy VALUES(true,'test-only',%s,%s,%s,3)",(3*S,3*S,S))
        if guard in ('own_test','own_allowed'):c.execute("INSERT INTO own_test_expense_profiles VALUES(%s,'test-only',%s,%s)",(A.owner_id,2*S,S))
        if guard=='legacy_cap':c.execute('UPDATE admission_limits SET expense_limit=%s WHERE owner_id=%s',(2*S,A.owner_id))
    factory=pg.ProductionGateways(store,env());factory.verify_vendor=lambda:None
    factory.provider_balance=lambda:{'response':{'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'1000'}]},'observed_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
    def response(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}],
            'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':2}})
    try:
        with tenant_context(A):
            if guard in ('own_test','session','platform'):
                seed=uuid.uuid4().hex;store.reserve_provider_attempt(job,seed,LAUNCH_Q|{'expense_units_per_usd':S},seed)
                store.transition_provider_attempt(seed,'start');store.transition_provider_attempt(seed,'settle',LAUNCH_USAGE)
                with psycopg.connect(DSN) as c:
                    n=c.execute('SELECT count(*) FROM auth.users').fetchone()[0]
                    spent=2*S if guard=='own_test' else 3*S*(n if guard=='platform' else 1)
                    c.execute('UPDATE provider_attempts SET expense_actual=%s,settled_at=%s WHERE attempt_id=%s',(spent,time.time(),seed))
                    if guard=='platform':c.execute('UPDATE expense_guard_policy SET session_units=%s',(spent+S,))
            v=operation();v['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,session_id=store.engine_session(tid),job_id=job,provider='deepseek',model=MODEL)
            op=decode_operation(frame(v));journal=ChildBrokerJournal(store,op);gw=factory(None,job)
            assert gw.launch_guard
            with httpx.Client(transport=httpx.MockTransport(response)) as client:
                broker=ChildProviderBroker(op,gateway=gw,journal=journal,client=client,endpoint='https://api.deepseek.com/chat/completions',headers={},authorize=lambda *a:None)
                value={'call_id':'call1','stage':'needs','provider':'deepseek','model':MODEL,
                    'serialized_request':base64.b64encode(json.dumps({'model':MODEL,'max_tokens':16,'messages':[{'role':'user','content':'fake'}]}).encode()).decode()}
                if guard in ('allowed','own_allowed'):
                    broker.dispatch(value);broker.dispatch(value)
                    assert len(calls)==1
                else:
                    with pytest.raises(BrokerPaused):broker.dispatch(value)
                    assert calls==[]
                    with pytest.raises(BrokerPaused):broker.dispatch(value)
            with store._postgres.transaction() as c:
                if guard in ('session','platform'):
                    assert c.execute('SELECT count(*) AS n FROM expense_alert_outbox').fetchone()['n']==1
                if guard=='legacy_cap':assert c.execute('SELECT expense_limit FROM admission_limits').fetchone()['expense_limit']==2*S
    finally:
        factory.close()
        with psycopg.connect(DSN) as c:
            c.execute('DELETE FROM expense_alert_outbox');c.execute('DELETE FROM expense_guard_episodes');c.execute('DELETE FROM expense_guard_policy')
            c.execute('DELETE FROM own_test_expense_profiles WHERE owner_id=%s',(A.owner_id,))
