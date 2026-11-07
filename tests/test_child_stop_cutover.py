"""Stop accepted work in a fresh real graph child. Fake HTTP, disposable PG."""
import asyncio,json,sys,threading,uuid,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from concurrent.futures import ThreadPoolExecutor
import pytest
from delivery_api.child_interview import ChildInterviewClient
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN


@pytest.mark.parametrize('reason',['stop','auth-revoked'])
def test_durable_stop_drains_accepted_fake_provider_but_never_publishes_next_question(store,tmp_path,reason):
    pytest.importorskip('interview_engine')
    accepted=threading.Event();release=threading.Event();calls=[];settled=[];uncertain=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload)
            accepted.set();assert release.wait(10)
            body=json.dumps({'id':'fake','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake'})},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    with tenant_context(A):
        tid=store.create_task('stop actual child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
    class Gateway:
        retail_rates=None
        def __init__(self,job):self.job_id=job;self.states={}
        def reserve_serialized(self,**kw):key=uuid.uuid4().hex;self.states[key]='reserved';return key
        def start(self,a):self.states[a]='started'
        def settle_raw(self,a,u):self.states[a]='settled';settled.append(u)
        def uncertain(self,a):self.states[a]='uncertain';uncertain.append(a)
        def _attempt(self,a):return {'state':self.states[a]}
    command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
    client=ChildInterviewClient(store,tmp_path,gateway_factory=lambda cap,job:Gateway(job),dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',command=command)
    detected=threading.Event();latency=[];changed=[None]
    original_run=client.supervisor.run
    async def measured_run(*args,**kwargs):
        async def detection():
            await kwargs['stop'].wait()
            latency.append(time.monotonic()-changed[0]);detected.set()
        observer=asyncio.create_task(detection())
        try:return await original_run(*args,**kwargs)
        finally:
            observer.cancel()
            try:await observer
            except asyncio.CancelledError:pass
    client.supervisor.run=measured_run
    def start():
        from vmodel_contracts.interactive import StartSessionRequest
        with tenant_context(A):
            try:client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Test web app',model_route='ollama',initial_model='fake'))
            except Exception:return
            raise AssertionError('stopped child returned success')
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future=ex.submit(start);assert accepted.wait(10)
            changed[0]=time.monotonic()
            with tenant_context(A):
                if reason=='stop':store.request_task_stop(tid);client.stop(sid)
                else:
                    import psycopg
                    with psycopg.connect(DSN) as admin:admin.execute('DELETE FROM auth.sessions WHERE id=%s',(A.session_id,))
            assert detected.wait(1.5),'stop/revocation check exceeded its unchanged one-second cadence'
            print('CONTROL_DETECTION',reason,round(latency[0],3))
            release.set();future.result(timeout=10)
        assert len(calls)==1 and client.supervisor.last_result.reaped
        if reason=='stop':assert len(settled)==1 and not uncertain
        else:
            # Revocation prevents durable result write under RLS. No fake zero
            # settlement: accepted result is held for reconciliation.
            assert not settled and len(uncertain)==1
        with tenant_context(A):
            if reason=='stop':assert client.snapshot(sid).open_input is None
    finally:
        release.set();server.shutdown();server.server_close()
        if reason=='auth-revoked':
            import psycopg
            with psycopg.connect(DSN) as admin:
                admin.execute('INSERT INTO auth.sessions(id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',(A.session_id,A.owner_id))
