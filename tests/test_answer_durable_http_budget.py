"""Real DurableGateways + ProductionGateways + signed HTTP answer, fake vendor only.

Disposable PG, fixture credit, localhost HTTP: no paid execution or live accounts.
Run with ANSWER_OPT_BASELINE=1 only while comparing the untouched implementation.
"""
from paid_child_fixture import installed_child_command
import json,os,sys,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import httpx
from fastapi.testclient import TestClient
from delivery_api import model_pins,production_gateway,postgres_scope
from delivery_api.child_paid_runtime import PaidChildRuntime
from delivery_api.child_interview import ChildInterviewHost
from delivery_api.answer_mode import from_environment
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN
from test_model_pins import repo,payload
MODEL='deepseek-flash'
from paid_child_fixture import paid_child


def test_real_durable_gateway_http_answer_budget(store,repo,tmp_path,monkeypatch,pg_task_app,caplog):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            from strict_provider import read_request
            value=read_request(self);calls.append(value)
            content={'is_web_app':True,'archetype':'saas_crud','confidence':.9,'reason':'fixture'} if 'is_web_app' in json.dumps(value) else {'question':'What does a booking look like?','covered':[],'thin':[],'skipped':[],'complete':False}
            message={'role':'assistant','content':json.dumps(content)}
            if value.get('tool_choice'):
                message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture','type':'function','function':{'name':value['tool_choice']['function']['name'],'arguments':json.dumps({'payload':content})}}]}
            body=json.dumps({'id':'fixture','model':MODEL,'choices':[{'index':0,'message':message,'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    class Runtime(PaidChildRuntime):
        def transport(self,provider,model,local):
            self.require(provider,model)
            return endpoint,{}
    client=None;gateways=None
    try:
        with paid_child(store,endpoint),tenant_context(A):
            import psycopg
            with psycopg.connect(DSN) as admin:admin.execute('UPDATE expense_guard_policy SET session_units=1000000000000,per_registered_user_units=1000000000000')
            pin=payload();pin['row']['model']=MODEL;pin['row']['provider_snapshot']['model']=MODEL;pin['context']['model']=MODEL
            repo.commit(store,pin,None,'Disposable test reviewed pin')
            monkeypatch.setattr(model_pins,'pin_verifier',lambda value:lambda:True) # vendor website only
            init=production_gateway.ProductionGateways.__init__
            def fake_balance(self,*a,**kw):
                kw['balance_transport']=httpx.MockTransport(lambda request:httpx.Response(200,json={'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'1000','granted_balance':'0','topped_up_balance':'1000'}]}))
                init(self,*a,**kw)
            monkeypatch.setattr(production_gateway.ProductionGateways,'__init__',fake_balance)
            gateways=model_pins.DurableGateways(store,{production_gateway.KEY_ENV:'fixture-only'})
            runtime=Runtime(models=gateways.models,api_key='fixture-only');runtime.pin_factory=gateways
            tid=store.create_task('durable HTTP '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4())
            assert store.begin_interview_start(tid,sid,'deepseek')
            client=ChildInterviewHost(store,tmp_path,gateway_factory=gateways,session_dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=endpoint,paid_runtime=runtime,answer_accounting=from_environment({},runtime)).client()
            client.supervisor.command=installed_child_command()
            from vmodel_contracts.interactive import StartSessionRequest
            state=client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='A booking app',model_route='deepseek',initial_model=MODEL)).snapshot
            from delivery_api import app as module,request_costs,cloud_runtime
            monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
            from pg_workspace import Workspace
            monkeypatch.setattr(module,'engine_client',client)
            ws=Workspace(DSN,store,A)
            samples=[];budgets=[]
            original_record=request_costs.record_request
            def record(method,path,status,duration,stats):
                if method=='POST' and path.endswith('/answer'):budgets.append(sum(e[3] for e in stats.trace))
                return original_record(method,path,status,duration,stats)
            monkeypatch.setattr(request_costs,'record_request',record)
            with TestClient(pg_task_app(ws),headers=ws.headers) as http:
                for turn in range(3):
                    before=len(calls)
                    response=http.post(f'/api/tasks/{tid}/answer',json={'answer':'A customer books a time slot','decision_id':str(state.open_input.input_id),'expected_sequence':state.sequence})
                    assert response.status_code==200,response.text
                    trace=client._last_answer_trace
                    assert trace['parent_wire_complete'] and trace['child']['complete']
                    request=next(e for e in reversed(request_costs._entries) if e['type'].startswith('POST') and e['status']==200)
                    state=client.snapshot(sid)
                    assert len(calls)==before+1 and state.open_input
                    samples.append({'turn':turn,'inner':trace['total_syncs'],'parent':trace['parent']['syncs'],'request_syncs':budgets[-1],'http_ms':request['duration_ms']})
                    if not os.getenv('ANSWER_OPT_BASELINE'):
                        assert not any('model_pins:list' in row['caller'] for row in trace['parent_wire'])
                        assert trace['parent']['syncs']<=22
                        assert budgets[-1]<=70
                print('DURABLE_HTTP_SAMPLES',json.dumps(samples))
            with store._postgres.transaction() as c:
                receipts=c.execute('SELECT state,actual_units FROM answer_charge_receipts WHERE session_id=%s',(sid,)).fetchall()
                assert len(receipts)==3 and all(r['state']=='settled' and r['actual_units']>0 for r in receipts)
            if not os.getenv('ANSWER_OPT_BASELINE'):
                assert samples[-1]['inner']<=31
                assert not any('progress.ingest.failed' in r.message or 'progress.drain.failed' in r.message for r in caplog.records)
                assert any(e['kind']=='progress' and e['payload'].get('source')=='interview' for e in store.events(tid))
                assert store.progress_cursor(tid,'interview',sid)<10**9
    finally:
        if client:client.close()
        if gateways:gateways.close()
        server.shutdown();server.server_close()
