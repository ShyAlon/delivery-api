"""Both real admitted wires and disposable ledger, no network/provider contact."""
import json,uuid
import httpx,pytest
from test_local_ollama_ledger import job,DSN,A
from test_production_gateway import dual_env,MODEL
from delivery_api import production_gateway as pg
from delivery_api.tenancy import tenant_context
pytestmark=pytest.mark.skipif(not DSN,reason='disposable Postgres required')

def test_alternating_jobs_keep_quotes_usage_and_money_on_the_selected_provider(job,monkeypatch):
    import psycopg
    from delivery_api.provider_network import deepseek_client_factory,ollama_client_factory
    store,tid,original=job;contacts=[];checks=[];jobs=[]
    monkeypatch.setenv('OLLAMA_BASE_URL','http://127.0.0.1:11434/v1')
    with psycopg.connect(DSN) as connection:
        connection.execute('UPDATE admission_limits SET expense_limit=NULL,retail_limit=NULL,attempt_expense_max=NULL,attempt_retail_max=NULL,rate_max=NULL,concurrency_max=3 WHERE owner_id=%s',(A.owner_id,))
        connection.execute("INSERT INTO expense_guard_policy VALUES(true,'test-only',%s,%s,%s,3)",(3*10**12,3*10**12,10**12))
        connection.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES('dual-fixture',1000000000000,1000000000000,%s,'fake fixture')",(A.owner_id,))
        connection.execute("INSERT INTO operator_expense_policy_head VALUES(true,'dual-fixture')")
    def balance(request):
        checks.append('balance')
        return httpx.Response(200,json={'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'1000'}]})
    factory=pg.ProductionGateways(store,dual_env(),balance_transport=httpx.MockTransport(balance))
    factory._providers['deepseek'].verify_vendor=lambda:checks.append('vendor')
    def wire(request):
        body=json.loads(request.content);provider='deepseek' if request.url.host=='api.deepseek.com' else 'ollama'
        contacts.append(provider)
        usage={'prompt_tokens':5,'completion_tokens':2,'total_tokens':7}
        if provider=='deepseek':usage.update(prompt_cache_hit_tokens=1,prompt_cache_miss_tokens=4)
        return httpx.Response(200,json={'id':'fake','object':'chat.completion','model':body['model'],
            'choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],'usage':usage})
    try:
        with tenant_context(A):
            store.finish_worker(original)
            for provider,model in [('ollama','phi4'),('deepseek',MODEL),('ollama','phi4'),('deepseek',MODEL)]:
                jid=store.enqueue_worker(tid,'phase:intent',execution_options={'model_route':provider+':'+model});jobs.append(jid);store.claim_worker(jid)
                gateway=factory(None,jid);before=list(checks)
                # This same property controls worker pre-invoke compute metering.
                assert (gateway.retail_rates is None)==(provider=='ollama')
                clients=(ollama_client_factory if provider=='ollama' else deepseek_client_factory)(inner_sync=lambda:httpx.MockTransport(wire),inner_async=lambda:httpx.MockTransport(wire),owner_module='interview_engine')
                sync,asynchronous=clients(stage='probing',provider=provider,model=model,gateway=gateway)
                try:
                    url='http://127.0.0.1:11434/v1/chat/completions' if provider=='ollama' else 'https://api.deepseek.com/chat/completions'
                    assert sync.post(url,json={'model':model,'max_tokens':16,'messages':[{'role':'user','content':'hi'}]}).status_code==200
                finally:sync.close();__import__('asyncio').run(asynchronous.aclose())
                with store._postgres.transaction() as connection:
                    rows=connection.execute('SELECT state,quote,usage,expense_actual,retail_actual FROM provider_attempts WHERE job_id=%s',(jid,)).fetchall()
                assert len(rows)==1
                row=rows[0];assert row['state']=='settled' and row['quote']['provider']==provider and row['quote']['model']==model
                assert row['usage']['input_tokens']==5 and row['usage']['output_tokens']==2
                if provider=='ollama':
                    assert row['expense_actual']==row['retail_actual']==0
                    assert checks==before
                else:
                    assert row['expense_actual']>0 and row['retail_actual']>0
                    assert checks.count('balance')==before.count('balance')+1 and checks.count('vendor')>before.count('vendor')
                store.finish_worker(jid)
        assert contacts==['ollama','deepseek','ollama','deepseek']
    finally:
        factory.close()
        with psycopg.connect(DSN) as connection:
            connection.execute('DELETE FROM expense_guard_policy')
            connection.execute('DELETE FROM operator_expense_policy_head');connection.execute('DELETE FROM operator_expense_policy_revisions')
            for jid in jobs:
                connection.execute('DELETE FROM retail_contributions WHERE owner_id=%s',(A.owner_id,))
                connection.execute('DELETE FROM credit_charges WHERE owner_id=%s',(A.owner_id,))
                connection.execute('DELETE FROM provider_attempts WHERE job_id=%s',(jid,))
