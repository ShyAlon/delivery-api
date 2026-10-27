"""Real application graph in a fresh process with fake local provider HTTP."""
import os,uuid,threading,json
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN
from delivery_api.child_interview import ChildInterviewClient


def test_actual_interview_start_runs_in_child_with_parent_broker(store,tmp_path,monkeypatch):
    # Force real imports; this test is run with both checked-out consumer sources.
    pytest.importorskip('interview_engine')
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['content-length']));payload=json.loads(raw);calls.append(payload)
            # Invalid semantic output intentionally exercises durable provider
            # dispatch and fails the graph without any real provider request.
            body=json.dumps({'id':'fake','object':'chat.completion','model':payload['model'],
                'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake web app'}) if 'is_web_app' in json.dumps(payload) else json.dumps({'question':'What does a booking look like for your customers?','covered':[],'thin':[],'skipped':[],'complete':False})},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tenant_context(A):
            tid=store.create_task('actual child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            # Fake parent gateway uses actual ledger? Explicit fake below records
            # every effect, while migrated Postgres still enforces owner/lease.
            class Gateway:
                retail_rates=None
                def __init__(self,job):self.job_id=job;self.states={}
                def reserve_serialized(self,**kw):self.states['fake']='reserved';return 'fake'
                def start(self,a):self.states[a]='started'
                def settle_raw(self,a,u):self.states[a]='settled'
                def uncertain(self,a):self.states[a]='uncertain'
                def _attempt(self,a):return {'state':self.states[a]}
            client=ChildInterviewClient(store,tmp_path,gateway_factory=lambda cap,job:Gateway(job),
                command=[__import__('sys').executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(__import__('pathlib').Path(__file__).parents[2]/'interview-engine/src'),str(__import__('pathlib').Path(__file__).parents[2]/'spec-generator/src')],dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions')
            from vmodel_contracts.interactive import StartSessionRequest
            result=client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='A test app',model_route='ollama',initial_model='fake'))
            assert result.snapshot.sequence>0
            assert client.supervisor.last_result.reaped
            assert client.snapshot(sid).sequence==result.snapshot.sequence
            assert result.snapshot.open_input is not None
            old_pid=client.supervisor.last_result.pid
            from vmodel_contracts.interactive import InputResponse
            answered=client.submit(sid,InputResponse(input_id=result.snapshot.open_input.input_id,
                value='Bookings contain a customer and a time slot',actor_id='fake',expected_sequence=result.snapshot.sequence))
            assert answered.snapshot.sequence>result.snapshot.sequence
            assert client.supervisor.last_result.pid!=old_pid and client.supervisor.last_result.reaped
            event=next(e for e in reversed(store.events(tid)) if e['kind']=='child.completed')
            job=event['payload']['job_id']
            # Simulate job-completion acknowledgement loss after durable graph,
            # exact compute and reap evidence. Recovery cannot spawn a graph.
            with store._postgres.transaction() as conn:
                conn.execute("UPDATE worker_jobs SET state='failed' WHERE job_id=%s",(job,))
            count=len(calls)
            recovery=ChildInterviewClient(store,tmp_path,gateway_factory=lambda *a:(_ for _ in ()).throw(AssertionError('no dispatch')),dsn=client.dsn,endpoint=client.endpoint)
            assert recovery.resume(job).sequence==answered.snapshot.sequence
            assert store.worker_job(job)['state']=='done' and len(calls)==count
            assert calls,'fresh child never reached the parent provider broker'
    finally:server.shutdown();server.server_close()


def test_paid_route_refused_before_mutations_or_launch(tmp_path):
    from vmodel_contracts.interactive import StartSessionRequest
    class Store:
        def __getattr__(self,name):raise AssertionError('paid refusal must precede store mutation')
    client=ChildInterviewClient(Store(),tmp_path,gateway_factory=None,dsn='unused',endpoint='http://127.0.0.1:1')
    with pytest.raises(PermissionError,match='paid'):client.start(StartSessionRequest(task_id='fake',idea='fake',model_route='deepseek'))
