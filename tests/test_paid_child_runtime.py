"""Disabled paid adapter seam: fake HTTP, actual children and disposable PG."""
import json,sys,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
from delivery_api.child_paid_runtime import PaidChildRuntime
from delivery_api.child_interview import ChildInterviewClient
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN,store
from paid_child_fixture import paid_child


def test_paid_interview_exact_parent_accounting_reaped_and_recovery_never_rebills(store,tmp_path):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload)
            content={'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake'} if 'is_web_app' in json.dumps(payload) else {'question':'What does a booking look like for customers?','covered':[],'thin':[],'skipped':[],'complete':False}
            message={'role':'assistant','content':json.dumps(content)};finish='stop'
            if payload.get('tools'):
                tool=payload['tools'][0]['function']['name']
                message={'role':'assistant','content':json.dumps(content),'tool_calls':[{'id':'fake-tool','type':'function','function':{'name':tool,'arguments':json.dumps({'payload':content})}}]};finish='tool_calls'
            body=json.dumps({'id':'fake','model':'deepseek-flash','choices':[{'index':0,'message':message,'finish_reason':finish}],
                'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
    try:
        with paid_child(store,endpoint) as (factory,runtime),tenant_context(A):
            tid=store.create_task('paid child fake '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            client=ChildInterviewClient(store,tmp_path,gateway_factory=factory,paid_runtime=runtime,dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=endpoint,command=command)
            original=client.supervisor.run
            async def checked(*args,**kwargs):
                config=json.dumps(kwargs['private_config'])
                assert 'fixture-only' not in config and 'Authorization' not in config
                assert 'compute' in kwargs['descriptors'] and kwargs['private_config']['provider']=='deepseek'
                return await original(*args,**kwargs)
            client.supervisor.run=checked
            from vmodel_contracts.interactive import StartSessionRequest,InputResponse
            result=client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='A test app',model_route='deepseek',initial_model='deepseek-flash'))
            assert client.supervisor.last_result.reaped and result.snapshot.open_input is not None
            first=client.supervisor.last_result.pid
            answered=client.submit(sid,InputResponse(input_id=result.snapshot.open_input.input_id,value='A customer and available time slot',actor_id='test',expected_sequence=result.snapshot.sequence))
            assert client.supervisor.last_result.reaped and client.supervisor.last_result.pid!=first
            job=next(e for e in reversed(store.events(tid)) if e['kind']=='child.completed')['payload']['job_id']
            with store._postgres.transaction() as conn:
                before=conn.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']
                rows=conn.execute("SELECT state,deterministic_ms FROM retail_compute_segments WHERE owner_id=%s",(A.owner_id,)).fetchall()
                assert len(rows)==2 and all(r['state']=='settled' and r['deterministic_ms']>0 for r in rows)
                assert conn.execute("SELECT count(*) AS n FROM retail_contributions WHERE kind='tokens'").fetchone()['n']==len(calls)
                conn.execute("UPDATE worker_jobs SET state='failed' WHERE job_id=%s",(job,))
            count=len(calls)
            fresh=ChildInterviewClient(store,tmp_path,gateway_factory=lambda *a:pytest.fail('recovery redispatched'),paid_runtime=runtime,dsn=client.dsn,endpoint=endpoint)
            assert fresh.resume(job).sequence==answered.snapshot.sequence and len(calls)==count
            with store._postgres.transaction() as conn:assert conn.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==before
            from vmodel_contracts.interactive import ModelRouteSwitchRequest
            current=answered.snapshot
            change=ModelRouteSwitchRequest(model_route='ollama',model='local-test',actor_id='test',
                expected_sequence=current.sequence,expected_input_id=current.open_input.input_id,idempotency_key=uuid.uuid4())
            switched=client.switch_route(sid,change)
            assert switched.snapshot.model_map['probing']==('ollama','local-test')
            assert client.supervisor.last_result.reaped and len(calls)==count
            with store._postgres.transaction() as conn:
                balance=conn.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']
                segments=conn.execute('SELECT count(*) AS n FROM retail_compute_segments').fetchone()['n']
            assert fresh.switch_route(sid,change).snapshot.sequence==switched.snapshot.sequence
            with store._postgres.transaction() as conn:
                assert conn.execute('SELECT balance_units FROM credit_accounts').fetchone()['balance_units']==balance
                assert conn.execute('SELECT count(*) AS n FROM retail_compute_segments').fetchone()['n']==segments

    finally:server.shutdown();server.server_close()


def test_paid_activation_selector_still_refuses():
    from delivery_api.child_pool_budget import child_mode
    with pytest.raises(ValueError,match='local only'):child_mode({'DELIVERY_CHILD_EXECUTION':'paid','DELIVERY_LOCAL_PREVIEW':'1'})


@pytest.mark.parametrize('reason',['stop','kill'])
def test_paid_child_interruption_keeps_accepted_money_without_redispatch(store,tmp_path,reason):
    import signal
    from concurrent.futures import ThreadPoolExecutor
    accepted=threading.Event();release=threading.Event();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload);accepted.set();assert release.wait(10)
            body=json.dumps({'id':'fake','model':'deepseek-flash','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake'})},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            try:self.wfile.write(body)
            except BrokenPipeError:pass
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    command=[sys.executable,'-c',"import sys,runpy,os;open(sys.argv[1],'w').write(str(os.getpid()));sys.path[:0]=sys.argv[2:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(tmp_path/'pid'),str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
    try:
        with paid_child(store,endpoint) as (factory,runtime):
            with tenant_context(A):
                tid=store.create_task('interrupted paid fake '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            client=ChildInterviewClient(store,tmp_path,gateway_factory=factory,paid_runtime=runtime,dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=endpoint,command=command)
            def start():
                from vmodel_contracts.interactive import StartSessionRequest
                with tenant_context(A),pytest.raises(Exception):client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Test app',model_route='deepseek',initial_model='deepseek-flash'))
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(start);assert accepted.wait(10)
                if reason=='stop':
                    with tenant_context(A):store.request_task_stop(tid);client.stop(sid)
                else:__import__('os').kill(int((tmp_path/'pid').read_text()),signal.SIGKILL)
                release.set();future.result(timeout=15)
            assert len(calls)==1 and client.supervisor.last_result.reaped
            with tenant_context(A),store._postgres.transaction() as conn:
                attempts=conn.execute('SELECT state,expense_actual FROM provider_attempts WHERE owner_id=%s',(A.owner_id,)).fetchall()
                assert len(attempts)==1 and attempts[0]['state']=='settled' and attempts[0]['expense_actual']==11
                assert conn.execute("SELECT count(*) AS n FROM retail_contributions WHERE kind='tokens'").fetchone()['n']==1
                segments=conn.execute('SELECT state FROM retail_compute_segments WHERE owner_id=%s',(A.owner_id,)).fetchall()
                assert len(segments)==1 and segments[0]['state'] in ('settled','unresolved')
                if reason=='kill':assert segments[0]['state']=='unresolved'  # No guessed process CPU.
                conn.execute('UPDATE engine_sessions SET lease_expires_at=0 WHERE session_id=%s',(sid,))
                job=conn.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s',(tid,)).fetchone()['job_id']
            with tenant_context(A),pytest.raises(PermissionError):client.resume(job)
            assert len(calls)==1
    finally:release.set();server.shutdown();server.server_close()


@pytest.mark.parametrize('field,value',[('launch_guard',False),('require_prepaid',False),('retail_rates',None),('job_id','foreign')])
def test_paid_runtime_refuses_incomplete_money_boundary(field,value):
    from types import SimpleNamespace
    from test_child_execution_contract import operation,frame
    from delivery_api.child_execution_contract import decode_operation
    store=object();runtime=PaidChildRuntime(models={'deepseek':'deepseek-flash'},api_key='parent-only')
    gateway=SimpleNamespace(store=store,job_id='job-1',require_prepaid=True,launch_guard=True,retail_rates=object(),admit_serialized=lambda:None)
    setattr(gateway,field,value)
    with pytest.raises(PermissionError,match='requires parent'):runtime.compute(store,decode_operation(frame(operation())),gateway,'needs',None)


def test_paid_transport_pins_key_and_model():
    from test_child_execution_contract import operation,frame
    from delivery_api.child_execution_contract import decode_operation
    from types import SimpleNamespace
    runtime=PaidChildRuntime(models={'deepseek':'deepseek-flash','ollama':'local-test'},api_key='parent-only')
    assert runtime.transport('deepseek','deepseek-flash','unused')==('https://api.deepseek.com/chat/completions',{'Authorization':'Bearer parent-only'})
    assert runtime.transport('ollama','local-test','http://local.test')==('http://local.test',{})
    with pytest.raises(PermissionError):runtime.transport('deepseek','foreign','unused')
