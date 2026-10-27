"""A killed graph child cannot trigger a blind provider retry."""
import json,os,signal,sys,threading,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
from delivery_api.child_interview import ChildInterviewClient
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,DSN


def test_sigkill_after_dispatch_leaves_replay_paused_not_second_provider_call(store,tmp_path):
    pytest.importorskip('interview_engine')
    accepted=threading.Event();release=threading.Event();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['content-length']))));accepted.set();release.wait(10)
            body=json.dumps({'id':'fake','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            try:self.wfile.write(body)
            except BrokenPipeError:pass
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    with tenant_context(A):
        tid=store.create_task('kill actual child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
    class Gateway:
        retail_rates=None
        def __init__(self,job):self.job_id=job;self.states={}
        def reserve_serialized(self,**kw):key=uuid.uuid4().hex;self.states[key]='reserved';return key
        def start(self,a):self.states[a]='started'
        def settle_raw(self,a,u):self.states[a]='settled'
        def uncertain(self,a):self.states[a]='uncertain'
        def _attempt(self,a):return {'state':self.states[a]}
    client=ChildInterviewClient(store,tmp_path,gateway_factory=lambda cap,job:Gateway(job),dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
        command=[sys.executable,'-c',"import sys,runpy,os;open(sys.argv[1],'w').write(str(os.getpid()));sys.path[:0]=sys.argv[2:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(tmp_path/'pid'),str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')])
    def start():
        from vmodel_contracts.interactive import StartSessionRequest
        with tenant_context(A),pytest.raises(Exception):client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Test web app',model_route='ollama',initial_model='fake'))
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future=ex.submit(start);assert accepted.wait(10)
            os.kill(int((tmp_path/'pid').read_text()),signal.SIGKILL);release.set();future.result(timeout=10)
        with tenant_context(A):
            jobs=store.recoverable_worker_jobs();job=next(j for j in jobs if j['task_id']==tid)
            # Expire only the throwaway killed child's lease, representing TTL.
            with store._postgres.transaction() as conn:conn.execute('UPDATE engine_sessions SET lease_expires_at=0 WHERE session_id=%s',(sid,))
            with pytest.raises(PermissionError,match='no published checkpoint|completion evidence'):client.resume(job['job_id'])
        assert len(calls)==1 and client.supervisor.last_result.reaped
    finally:release.set();server.shutdown();server.server_close()
