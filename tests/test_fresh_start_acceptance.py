"""Required cross-repo journey. No skips: real startup, HTTP, child, PG; fake vendor only."""
import importlib.util
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import httpx
import uvicorn
import pytest

pytestmark=[pytest.mark.engine_integration,pytest.mark.producer_integration]
from delivery_api.tenancy import tenant_context


def test_fresh_zero_credit_http_start(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path):
    # Missing producer packages are errors, not a silently skipped acceptance.
    import interview_engine, spec_generator
    from delivery_api import app as module,cloud_runtime
    from test_local_ollama_ledger import local_env,MODEL
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            body=json.dumps({'data':[{'id':MODEL}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload)
            content=({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake web app'}
                     if 'is_web_app' in json.dumps(payload) else
                     {'question':'What does a booking look like for your customers?','covered':[],'thin':[],'skipped':[],'complete':False})
            body=json.dumps({'id':'fake','object':'chat.completion','model':MODEL,'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    vendor=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=vendor.serve_forever,daemon=True).start()
    env=pg_runtime_environment()
    env.update(local_env(MODEL));env.update(DELIVERY_CLOUD_EXECUTION='1',DELIVERY_CHILD_EXECUTION='local',INTERVIEW_ENGINE_TOKEN='acceptance-not-a-live-secret-0000',OLLAMA_BASE_URL=f'http://127.0.0.1:{vendor.server_port}/v1',INTERVIEW_ENGINE_OLLAMA_MODEL=MODEL)
    for key,value in env.items():monkeypatch.setenv(key,value)
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    # The same preview startup schema/grants checks, against the real app role.
    spec=importlib.util.spec_from_file_location('acceptance_schema',Path(__file__).parents[1]/'scripts/ops/schema-readiness.py');schema=importlib.util.module_from_spec(spec);spec.loader.exec_module(schema)
    import psycopg
    with psycopg.connect(env['DELIVERY_DATABASE_URL'],autocommit=True) as conn:
        conn.execute('SET default_transaction_read_only=on')
        assert all(r['status']=='present' for r in schema.inventory(conn))
        assert schema.grants(conn)==[]
    spec=importlib.util.spec_from_file_location('acceptance_preflight',Path(__file__).parents[1]/'scripts/docker/preflight.py')
    import sys
    preflight=importlib.util.module_from_spec(spec);sys.modules[spec.name]=preflight;spec.loader.exec_module(preflight)
    report=preflight.run(env)
    assert not [c for c in report.checks if c.level=='refuse' and c.status!='pass'],report
    with pg_workspace_factory() as workspace:
        with workspace.admin() as c:
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,0,1000000000,0)',(workspace.scope.owner_id,))
            c.execute('INSERT INTO admission_limits VALUES(%s,%s,86400,NULL,NULL,NULL,NULL,60,NULL,3)',(workspace.scope.owner_id,'acceptance'))
        middleware=pg_task_app(workspace)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(middleware,host='127.0.0.1',port=port,lifespan='on',log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run);thread.start()
        try:
            for _ in range(100):
                if server.started:break
                assert thread.is_alive(),'actual application startup failed'
                time.sleep(.05)
            assert server.started and cloud_runtime.installed(),cloud_runtime.failure()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers=workspace.headers,timeout=20) as client:
                body={'title':'Acceptance recycled name','brief':'A booking app for a small salon'}
                first=client.post('/api/tasks',json=body);assert first.status_code==201,first.text
                tid=first.json()['task_id']
                deleted=client.request('DELETE','/api/tasks/'+tid,json={'confirm':True});assert deleted.status_code==200,deleted.text
                created=client.post('/api/tasks',json=body);assert created.status_code==201,created.text
                tid=created.json()['task_id']
                start=client.post(f'/api/tasks/{tid}/start',json={'model_route':'ollama'});assert start.status_code==202,start.text
                deadline=time.monotonic()+35
                while time.monotonic()<deadline:
                    with tenant_context(workspace.scope):
                        task=workspace.store.task(tid);events=workspace.store.events(tid)
                    assert not any(p['state']=='failed' for p in task['phases']),events
                    if any(e['kind']=='child.completed' for e in events):break
                    time.sleep(.1)
                else:raise AssertionError('first interview stage never completed: '+repr(events))
                assert calls,'HTTP202 is not acceptance; provider must be reached'
                with tenant_context(workspace.scope):
                    snapshot=module.engine_client.snapshot(start.json()['session_id'])
                    assert snapshot.sequence>0 and snapshot.open_input is not None
                assert not any(e['kind']=='error' for e in events),events
                with workspace.admin() as c:
                    rows=c.execute('SELECT state,expense_actual,retail_actual,usage FROM provider_attempts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchall()
                    assert rows and all(r[0]=='settled' and r[1]==r[2]==0 and r[3] for r in rows),rows
                    assert c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()==(0,)
        finally:
            server.should_exit=True;thread.join(10);vendor.shutdown();vendor.server_close()
            assert not thread.is_alive()
