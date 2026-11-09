"""End-to-end warm child: two HTTP answers served by one child process.

The first answer spawns a warm child (UI: 'Warming up a fresh session'),
parks it on the session once the answer publishes WAITING_INPUT, and the
second answer reuses the parked process (UI: 'Reusing an existing session'):
same pid, no second claim (lease epoch unchanged), one claim total. A stop
recycles the parked child. Real child_entry subprocess, real PG, fake
provider over loopback HTTP.
"""
import json,sys,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
SIBLINGS=Path(__file__).parents[2]
sys.path[:0]=[str(SIBLINGS/'spec-generator/src'),str(SIBLINGS/'interview-engine/src')]
from test_cloud_api import cloud,header,A  # noqa: F401


def test_two_answers_one_child_process(cloud,monkeypatch):
    pytest.importorskip('interview_engine')
    client,store,root=cloud
    import delivery_api.app as module
    from delivery_api.child_interview import ChildInterviewClient
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import DSN
    from test_cloud_api import A as scope
    from strict_provider import read_request
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=read_request(self);calls.append(payload)
            body=json.dumps({'id':'fake','object':'chat.completion','model':payload['model'],'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake'}) if 'is_web_app' in json.dumps(payload) else json.dumps({'question':'What does a booking look like for your customers?','covered':[],'thin':[],'skipped':[],'complete':False})},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    ledger={}
    class Gateway:
        retail_rates=None
        def __init__(self,job):self.job_id=job;self.states=ledger
        def reserve_serialized(self,**kw):self.states['fake']='reserved';return 'fake'
        def start(self,a):self.states[a]='started'
        def settle_raw(self,a,u):self.states[a]='settled'
        def uncertain(self,a):self.states[a]='uncertain'
        def _attempt(self,a):return {'state':self.states[a]}
    child=ChildInterviewClient(store,root,gateway_factory=lambda cap,job:Gateway(job),dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
        command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(SIBLINGS/'interview-engine/src'),str(SIBLINGS/'spec-generator/src')])
    from delivery_api import cloud_runtime
    monkeypatch.setattr(cloud_runtime,'installed',lambda:True)
    monkeypatch.setattr(module,'engine_client',child)
    if hasattr(module,'cloud_execution_admitted'):monkeypatch.setattr(module,'cloud_execution_admitted',lambda:True)
    try:
        h=header('a')
        tid=client.post('/api/tasks',json={'title':'warm child '+uuid.uuid4().hex},headers=h).json()['task_id']
        sid=str(uuid.uuid4())
        from vmodel_contracts.interactive import StartSessionRequest
        with tenant_context(scope):
            assert store.begin_interview_start(tid,sid,'ollama')
            snap=child.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Test web app',model_route='ollama',initial_model='fake')).snapshot

        first=client.post('/api/tasks/%s/answer'%tid,json={'answer':'Bookings have a customer and a time slot','decision_id':str(snap.open_input.input_id)},headers=h)
        assert first.status_code==200,first.text
        with tenant_context(scope):
            snap2=child.snapshot(sid)
            parked=child._warm.get(sid)
            assert parked is not None,'answer on a waiting session must park the warm child'
            handle=parked['handle'];pid=handle.process.pid
            first_epoch=parked['operation'].binding.lease_epoch
            held_epoch=store.session_binding(sid)['lease_epoch']
            assert held_epoch==first_epoch+1,'the warm child claims the lease row exactly once'
            delta=snap2.sequence-snap.sequence
            assert delta>=1,'answer must advance the published sequence'
            events=[e.get('message') for e in store.events(tid) if e['kind']=='progress']
            assert 'Warming up a fresh session' in events

        second=client.post('/api/tasks/%s/answer'%tid,json={'answer':'A customer picks a slot and gets a confirmation','decision_id':str(snap2.open_input.input_id)},headers=h)
        assert second.status_code==200,second.text
        with tenant_context(scope):
            snap3=child.snapshot(sid)
            parked2=child._warm.get(sid)
            assert parked2 is not None and parked2['handle'] is handle,'second answer must reuse the parked child'
            assert handle.process.pid==pid,'second answer spawned a new child process'
            assert parked2['operation'].binding.lease_epoch==held_epoch,'warm follow-up must pin the held epoch'
            assert store.session_binding(sid)['lease_epoch']==held_epoch,'no second claim across the follow-up answer'
            assert snap3.sequence-snap2.sequence==delta,'second answer must advance exactly like the first'
            events=[e.get('message') for e in store.events(tid) if e['kind']=='progress']
            assert 'Reusing an existing session' in events

        with tenant_context(scope):
            store.request_task_stop(tid)
            child.stop(sid)
            assert sid not in child._warm,'stop must recycle the parked child'
        assert handle.process.returncode==0,'recycled warm child must exit cleanly'
    finally:
        child.close();server.shutdown();server.server_close()
