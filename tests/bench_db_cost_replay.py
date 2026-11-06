"""DB-cost replay harness (opt-in: DB_COST_REPLAY=1). Not part of the normal suite.

Boots the real app against isolated Postgres, runs a real child with a fake provider
that answers after a fixed delay, then replays the hot endpoints (task detail, phase
output, credits, POST /answer) and prints one JSON line per request with db_queries,
db_ms and duration_ms from the app's own request_costs record. Copy this file into a
checkout of any candidate commit and run it there to compare commits."""
import json,os,sys,threading,time,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
import pytest
SIBLINGS=Path(os.getenv('DB_COST_SIBLINGS',str(Path(__file__).parents[2])))
sys.path[:0]=[str(SIBLINGS/'spec-generator/src'),str(SIBLINGS/'interview-engine/src')]
from test_cloud_api import cloud,header,A  # noqa: F401
pytestmark=pytest.mark.skipif(not os.getenv('DB_COST_REPLAY'),reason='opt-in measurement harness')
DELAY=float(os.getenv('DB_COST_PROVIDER_DELAY','3'))

def test_replay_hot_endpoints(cloud,monkeypatch):
    pytest.importorskip('interview_engine')
    client,store,root=cloud
    import delivery_api.app as module
    from delivery_api import request_costs
    from delivery_api.child_interview import ChildInterviewClient
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import DSN
    from test_cloud_api import A as scope
    from strict_provider import read_request
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=read_request(self);calls.append(payload)
            if len(calls)>1:time.sleep(DELAY)
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
        tid=client.post('/api/tasks',json={'title':'cost replay '+uuid.uuid4().hex},headers=h).json()['task_id']
        sid=str(uuid.uuid4())
        from vmodel_contracts.interactive import StartSessionRequest
        with tenant_context(scope):
            assert store.begin_interview_start(tid,sid,'ollama')
            snap=child.start(StartSessionRequest(session_id=sid,task_id=tid,idea='Test web app',model_route='ollama',initial_model='fake')).snapshot
        import collections
        from delivery_api import postgres_scope
        hist=collections.Counter()
        original=request_costs.record_request
        def recorded(method,path,status,duration_ms,stats):
            if method=='POST' and path.endswith('/answer'):
                hist.update(label for label,ms in stats.statements)
            return original(method,path,status,duration_ms,stats)
        monkeypatch.setattr(request_costs,'record_request',recorded)
        if os.getenv('DB_COST_NO_PROGRESS_WATCH'):
            async def no_watch(*args):pass
            monkeypatch.setattr(module,'_watch_engine_advance',no_watch)
        mark=len(request_costs._entries)
        for path in ['/api/tasks/%s'%tid,'/api/tasks/%s/phase-output'%tid,'/api/account/credits','/api/tasks/%s'%tid]:
            client.get(path,headers=h)
        r=client.post('/api/tasks/%s/answer'%tid,json={'answer':'Bookings have a customer and a time slot','decision_id':str(snap.open_input.input_id),'expected_sequence':snap.sequence},headers=h)
        client.get('/api/tasks/%s'%tid,headers=h)
        for e in list(request_costs._entries)[mark:]:
            print('DBCOST '+json.dumps({k:e[k] for k in ('type','status','duration_ms','db_queries','db_ms','held_ms')}))
        for sql,n in hist.most_common(16):print('DBTOP',n,sql)
        print('DBCOST_ANSWER_STATUS',r.status_code,r.text[:300])
        assert r.status_code==200,r.text
    finally:server.shutdown();server.server_close()
