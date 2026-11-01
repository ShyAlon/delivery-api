"""Migrated PG, real parent broker/admission, fake HTTP only. No provider spend."""
import base64
import json
import httpx
import psycopg
import pytest
from test_attempt_ledger import ledger
from test_postgres_store import A,DSN
from test_child_execution_contract import operation,frame
from delivery_api.child_execution_contract import decode_operation
from delivery_api.child_provider_broker import ChildProviderBroker
from delivery_api.child_broker_journal import ChildBrokerJournal
from delivery_api.production_gateway import ProductionGateways
from test_local_ollama_route import local_env,LOCAL_MODEL
from delivery_api.tenancy import tenant_context
from delivery_api.attempt_ledger import AdmissionDenied

@pytest.mark.parametrize('balance',[None,0])
def test_free_local_without_credit_settles_once(ledger,balance):
    store,tid,job=ledger;calls=[]
    with psycopg.connect(DSN) as c:
        c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))
        if balance is not None:c.execute('INSERT INTO credit_accounts VALUES (%s,0,1000000000,0)',(A.owner_id,))
    value=operation();value['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job,provider='ollama',model=LOCAL_MODEL)
    with tenant_context(A):value['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(value));factory=ProductionGateways(store,local_env());gateway=factory(None,job)
    def provider(req):
        calls.append(req)
        with psycopg.connect(DSN) as c:assert c.execute('SELECT state FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()==('dispatched',)
        return httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
    client=httpx.Client(transport=httpx.MockTransport(provider),trust_env=False)
    raw=json.dumps({'model':LOCAL_MODEL,'max_tokens':10,'messages':[]}).encode()
    request={'call_id':'free-1','stage':'intent','provider':'ollama','model':LOCAL_MODEL,'serialized_request':base64.b64encode(raw).decode()}
    try:
        with tenant_context(A):
            journal=ChildBrokerJournal(store,op)
            broker=ChildProviderBroker(op,gateway=gateway,journal=journal,client=client,endpoint='http://localhost/v1/chat/completions',headers={},authorize=lambda *a:None)
            first=broker.dispatch(request);assert broker.dispatch(request)==first
            row=journal.load((op.operation_id,'free-1','intent'));attempt=store.provider_attempt(row['attempt_id'])
            assert attempt['state']=='settled' and attempt['expense_actual']==attempt['retail_actual']==0
            assert attempt['usage']=={'input_tokens':2,'output_tokens':3}
        assert len(calls)==1
        with psycopg.connect(DSN) as c:assert c.execute('SELECT count(*) FROM credit_charges WHERE owner_id=%s',(A.owner_id,)).fetchone()==(0,)
    finally:
        client.close();factory.close()
        with psycopg.connect(DSN) as c:
            c.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))
            c.execute('DELETE FROM credit_charges WHERE owner_id=%s',(A.owner_id,))
            c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))

@pytest.mark.parametrize('provider,expense,retail',[('deepseek',0,0),('deepseek',1,0),('ollama',1,0),('ollama',0,1)])
def test_other_quotes_still_require_credit(ledger,provider,expense,retail):
    from delivery_api.prepaid import authorize
    store,tid,job=ledger
    with psycopg.connect(DSN) as c:c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))
    from delivery_api.postgres_store import _transactional
    @_transactional
    def check(s):
        authorize(s,{'attempt_id':'not-dispatched','quote':{'provider':provider,'expense_input':expense,'expense_output':0,'retail_input':retail,'retail_output':0}})

    with tenant_context(A),pytest.raises(AdmissionDenied,match='positive prepaid balance required'):check(store)
