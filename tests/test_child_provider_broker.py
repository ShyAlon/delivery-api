"""Parent-only broker: fake HTTP and durable-journal fault injection."""
import base64
import json
from types import SimpleNamespace
import httpx
import pytest
from delivery_api.child_provider_broker import ChildProviderBroker,BrokerPaused
from test_child_execution_contract import operation,frame
from test_attempt_ledger import ledger
from delivery_api.child_execution_contract import decode_operation

RAW=json.dumps({'model':'deepseek-flash','max_tokens':10,'messages':[]}).encode()
BODY=b'{"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5,"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":2}}'

class Journal:
    def __init__(self):self.row=None;self.fault=None
    def load(self,key):return self.row
    def begin(self,key,digest,provider,model):
        assert self.row is None
        self.row={'request_sha256':digest,'provider':provider,'model':model,'attempt_id':None,'response':None}
    def bind(self,key,attempt):self.row['attempt_id']=attempt
    def complete(self,key,response,usage):
        if self.fault=='complete':raise OSError('durability failed')
        self.row.update(response=response,usage=usage)

class Gateway:
    job_id='job-1'
    def __init__(self):self.states={};self.calls=[]
    def reserve_serialized(self,**kw):self.calls.append('reserve');self.states[kw['attempt_id']]='reserved';return kw['attempt_id']
    def start(self,attempt):self.calls.append('start');self.states[attempt]='dispatched'
    def uncertain(self,attempt):self.calls.append('uncertain');self.states[attempt]='uncertain'
    def settle_raw(self,attempt,usage):self.calls.append('settle');self.states[attempt]='settled'
    def _attempt(self,attempt):return {'state':self.states[attempt]}


def setup(handler=None):
    gateway=Gateway();journal=Journal();calls=[]
    def transport(request):
        calls.append(request)
        assert request.url==httpx.URL('https://fixed.invalid/v1/chat/completions')
        assert request.headers['authorization']=='Bearer parent-only'
        return handler(request) if handler else httpx.Response(200,content=BODY)
    client=httpx.Client(transport=httpx.MockTransport(transport),trust_env=False,follow_redirects=False)
    op=decode_operation(frame(operation()))
    broker=ChildProviderBroker(op,gateway=gateway,journal=journal,client=client,
        endpoint='https://fixed.invalid/v1/chat/completions',headers={'Authorization':'Bearer parent-only'},
        authorize=lambda op,call:None)
    return broker,gateway,journal,calls


def request():return {'call_id':'call-1','stage':'intent','provider':'deepseek','model':'deepseek-flash','serialized_request':base64.b64encode(RAW).decode()}


def test_parent_dispatches_exact_bytes_then_replays_without_dispatch_or_double_charge():
    broker,g,j,calls=setup();reply=broker.dispatch(request())
    assert calls[0].content==RAW and base64.b64decode(reply['body'])==BODY
    assert g.calls==['reserve','start','settle']
    assert broker.dispatch(request())==reply and len(calls)==1 and g.calls==['reserve','start','settle']


@pytest.mark.parametrize('change',[{'url':'https://attacker.invalid'},{'model':'foreign'},{'provider':'ollama'},{'headers':{}},{'settle':{}},{'serialized_request':'bad'},{'call_id':'../bad'}])
def test_foreign_or_effectful_input_cannot_reserve(change):
    broker,g,j,calls=setup();value=request();value.update(change)
    with pytest.raises((ValueError,BrokerPaused)):broker.dispatch(value)
    assert calls==[] and g.calls==[]


def test_unknown_after_dispatch_is_held_and_never_retried():
    def fail(request):raise httpx.ReadTimeout('ambiguous provider')
    broker,g,j,calls=setup(fail)
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert 'uncertain' in g.calls
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert len(calls)==1 and g.calls.count('reserve')==1


def test_raw_result_durability_failure_never_returns_success_or_retries():
    broker,g,j,calls=setup();j.fault='complete'
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert g.calls==['reserve','start','uncertain']
    j.fault=None
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert len(calls)==1


def test_completed_raw_response_can_settle_once_without_redispatch():
    broker,g,j,calls=setup()
    settle=g.settle_raw
    def fail(attempt,usage):raise OSError('settlement ack missing')
    g.settle_raw=fail
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert j.row['response'] is not None
    g.settle_raw=settle
    assert broker.dispatch(request())['status']==200 and len(calls)==1


def test_authority_rechecked_on_replay():
    broker,g,j,calls=setup();broker.dispatch(request())
    def deny(*a):raise PermissionError('auth revoked')
    broker.authorize=deny
    with pytest.raises(PermissionError):broker.dispatch(request())
    assert len(calls)==1


def test_conflicting_replay_never_reserves_again():
    broker,g,j,calls=setup();broker.dispatch(request())
    value=request();value['serialized_request']=base64.b64encode(RAW.replace(b'10',b'11')).decode()
    with pytest.raises(BrokerPaused,match='conflicts'):broker.dispatch(value)
    assert len(calls)==1 and g.calls.count('reserve')==1


def test_tampered_durable_response_is_not_delivered():
    broker,g,j,calls=setup();broker.dispatch(request());j.row['response']['body']=base64.b64encode(b'tampered').decode()
    with pytest.raises(BrokerPaused,match='hash'):broker.dispatch(request())
    assert len(calls)==1


def test_existing_uncertain_result_never_auto_settles():
    broker,g,j,calls=setup();broker.dispatch(request());g.states[j.row['attempt_id']]='uncertain'
    with pytest.raises(BrokerPaused,match='uncertain'):broker.dispatch(request())
    assert len(calls)==1 and g.calls.count('settle')==1


def test_durable_journal_survives_new_broker_instance(ledger):
    import hashlib
    from delivery_api.child_broker_journal import ChildBrokerJournal
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A,B
    store,tid,job=ledger
    value=operation();value['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,
        task_id=tid,job_id=job)
    with tenant_context(A):value['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(value));journal=ChildBrokerJournal(store,op);key=(op.operation_id,'call-1','intent')
    digest=hashlib.sha256(RAW).hexdigest();response={'status':200,'content_type':'application/json','body':base64.b64encode(BODY).decode(),'sha256':hashlib.sha256(BODY).hexdigest()}
    with tenant_context(A):
        journal.begin(key,digest,'deepseek','deepseek-flash');journal.bind(key,'attempt-1');journal.complete(key,response,{'tokens':1})
        fresh=ChildBrokerJournal(store,op)
        assert fresh.load(key)['response']==response
        with pytest.raises(Exception):fresh.begin(key,digest,'deepseek','deepseek-flash')
        with pytest.raises(Exception):fresh.complete(key,{'different':True},{})
    with tenant_context(B),pytest.raises(PermissionError):journal.load(key)
    # Fixture cleanup expects the task's dependent staging rows gone first.
    import psycopg
    from test_postgres_store import DSN
    with psycopg.connect(DSN) as conn:
        conn.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))
        conn.execute('DELETE FROM retail_contributions WHERE job_id=%s',(job,))


def test_broker_actual_ledger_settles_once_and_reuses_durable_bytes(ledger):
    import hashlib,uuid
    from delivery_api.producer_admission import WorkerAdmissionGateway
    from delivery_api.child_broker_journal import ChildBrokerJournal
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A,DSN
    from test_attempt_ledger import Q
    import psycopg
    store,tid,job=ledger;calls=[]
    value=operation();value['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job)
    with tenant_context(A):value['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(value))
    quote={**Q,'provider':'deepseek','model':'deepseek-flash'}
    gateway=WorkerAdmissionGateway(store,job,require_prepaid=False,
        resolve_serialized=lambda **kw:(quote,hashlib.sha256(kw['serialized_request']).hexdigest()),
        measure_raw_usage=lambda raw:{'input_tokens':raw['prompt_tokens'],'output_tokens':raw['completion_tokens']})
    def provider(request):
        # Provider sees only committed dispatch and its journal binding.
        with psycopg.connect(DSN) as conn:
            row=conn.execute("SELECT p.state FROM provider_attempts p JOIN staged_provider_responses r ON r.attempt_id=p.attempt_id WHERE p.job_id=%s",(job,)).fetchone()
            assert row==('dispatched',)
        calls.append(request);return httpx.Response(200,content=BODY)
    client=httpx.Client(transport=httpx.MockTransport(provider),trust_env=False)
    with tenant_context(A):
        journal=ChildBrokerJournal(store,op)
        broker=ChildProviderBroker(op,gateway=gateway,journal=journal,client=client,endpoint='https://fixed.invalid/chat/completions',headers={},authorize=lambda *a:None)
        first=broker.dispatch(request());assert broker.dispatch(request())==first
        row=journal.load((op.operation_id,'call-1','intent'));attempt=store.provider_attempt(row['attempt_id'])
        assert attempt['state']=='settled' and attempt['expense_actual']==8 and attempt['retail_actual']==18
        assert len(calls)==1
    client.close()
    with psycopg.connect(DSN) as conn:
        conn.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))
        conn.execute('DELETE FROM retail_contributions WHERE job_id=%s',(job,))


def test_terminal_sse_is_durable_before_return_and_replay():
    usage=json.loads(BODY)['usage']
    body=b'data: '+json.dumps({'choices':[],'usage':usage}).encode()+b'\r\n\r\ndata: [DONE]\r\n\r\n'
    broker,g,j,calls=setup(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},content=body))
    assert base64.b64decode(broker.dispatch(request())['body'])==body
    assert j.row['usage']==usage and g.calls==['reserve','start','settle']


@pytest.mark.parametrize('body',[b'data: [DONE]\n\n',b'data: {"choices":[],"usage":{}}\n\n',b'data: {"choices":[{"delta":{"content":"x"}}],"usage":{}}\n\ndata: [DONE]\n\n'])
def test_incomplete_sse_never_settles_or_returns(body):
    broker,g,j,calls=setup(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},content=body))
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert 'settle' not in g.calls and g.calls[-1]=='uncertain'


def test_authority_live_job_and_model_checked_before_dispatch(ledger):
    from delivery_api.child_broker_authority import ChildBrokerAuthority
    from delivery_api.tenancy import tenant_context,TenantAccessDenied
    from delivery_api.child_provider_broker import ProviderCall
    from test_postgres_store import A
    store,tid,job=ledger
    data=operation();data['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job)
    with tenant_context(A):data['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(data));authority=ChildBrokerAuthority(store,op,lease_epoch=1)
    with tenant_context(A),pytest.raises(TenantAccessDenied):authority(op,ProviderCall.model_validate(request()))


def test_authority_detects_stop_between_reserve_and_start():
    broker,g,j,calls=setup();checks=[]
    def auth(*a):
        checks.append(1)
        if len(checks)==2:raise PermissionError('stop persisted')
    broker.authorize=auth
    with pytest.raises(BrokerPaused):broker.dispatch(request())
    assert calls==[] and 'start' not in g.calls


def test_raw_response_before_settlement_has_no_blind_retry_even_on_new_instance():
    broker,g,j,calls=setup();broker.dispatch(request())
    fresh=ChildProviderBroker(broker.operation,gateway=g,journal=j,client=broker.client,
        endpoint=broker.endpoint,headers=broker.headers,authorize=broker.authorize)
    assert fresh.dispatch(request())==j.row['response'] and len(calls)==1


def test_live_authority_positive_then_stop_and_revocation(ledger):
    import psycopg,time
    from psycopg.types.json import Jsonb
    from delivery_api.child_broker_authority import ChildBrokerAuthority
    from delivery_api.child_provider_broker import ProviderCall
    from delivery_api.tenancy import tenant_context,TenantAccessDenied
    from test_postgres_store import A,DSN
    store,tid,job=ledger
    with tenant_context(A):sid=store.worker_job(job)['session_id']
    with psycopg.connect(DSN) as conn:
        conn.execute('UPDATE worker_jobs SET execution_options=%s WHERE job_id=%s',(Jsonb({'model_route':'deepseek:deepseek-flash'}),job))
        conn.execute('UPDATE engine_sessions SET lease_epoch=2,lease_expires_at=%s WHERE session_id=%s',(time.time()+60,sid))
    data=operation();data['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,session_id=sid,job_id=job)
    op=decode_operation(frame(data));authority=ChildBrokerAuthority(store,op,lease_epoch=2)
    call=ProviderCall.model_validate(request())
    with tenant_context(A):authority(op,call)
    with psycopg.connect(DSN) as conn:conn.execute('UPDATE tasks SET stop_requested_at=%s WHERE task_id=%s',(time.time(),tid))
    with tenant_context(A),pytest.raises(TenantAccessDenied):authority(op,call)
    with psycopg.connect(DSN) as conn:conn.execute('UPDATE tasks SET stop_requested_at=NULL WHERE task_id=%s',(tid,))


@pytest.mark.parametrize('raw',[
    b'{"model":"foreign","model":"deepseek-flash","max_tokens":10}',
    b'{"model":"deepseek-flash","max_tokens":9,"max_tokens":10}',
    b'{"model":"deepseek-flash","max_tokens":10,"temperature":NaN}'])
def test_shared_exact_contract_rejects_ambiguity_before_journal_or_reserve(raw):
    broker,g,j,calls=setup();value=request();value['serialized_request']=base64.b64encode(raw).decode()
    with pytest.raises(ValueError):broker.dispatch(value)
    assert j.row is None and not g.calls and not calls


@pytest.mark.parametrize('fault',['binding','authority','start'])
def test_atomic_parent_admission_rolls_back_attempt_and_binding_before_http(ledger,monkeypatch,fault):
    import hashlib
    from delivery_api.producer_admission import WorkerAdmissionGateway
    from delivery_api.child_broker_journal import ChildBrokerJournal
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A,DSN
    from test_attempt_ledger import Q
    import psycopg
    store,tid,job=ledger;data=operation()
    data['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job)
    with tenant_context(A):data['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(data));quote=Q|{'provider':'deepseek','model':'deepseek-flash'}
    gateway=WorkerAdmissionGateway(store,job,require_prepaid=False,
        resolve_serialized=lambda **kw:(quote,hashlib.sha256(kw['serialized_request']).hexdigest()))
    journal=ChildBrokerJournal(store,op);calls=[];count=[0]
    def fail():raise RuntimeError('injected '+fault)
    def authorize(*args):
        count[0]+=1
        if fault=='authority' and count[0]==2:fail()
    if fault=='binding':
        original=journal.bind
        def bind(*args):original(*args);fail()
        monkeypatch.setattr(journal,'bind',bind)
    if fault=='start':
        original=store.transition_provider_attempt
        def transition(a,action,*args,**kwargs):
            result=original(a,action,*args,**kwargs)
            if action=='start':fail()
            return result
        monkeypatch.setattr(store,'transition_provider_attempt',transition)
    with httpx.Client(transport=httpx.MockTransport(lambda req:calls.append(req)),trust_env=False) as client,tenant_context(A):
        broker=ChildProviderBroker(op,gateway=gateway,journal=journal,client=client,endpoint='https://fixed.invalid/chat/completions',headers={},authorize=authorize)
        with pytest.raises(BrokerPaused):broker.dispatch(request())
        row=journal.load((op.operation_id,'call-1','intent'))
        assert row['attempt_id'] is None and not calls
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT count(*) AS n FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()['n']==0
        with pytest.raises(BrokerPaused,match='incomplete'):broker.dispatch(request())
        assert not calls
    with psycopg.connect(DSN) as conn:conn.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))
