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


@pytest.mark.parametrize('paid',[False,True])
def test_fresh_zero_credit_http_start(pg_workspace_factory,pg_task_app,pg_runtime_environment,monkeypatch,tmp_path,paid,owner_acceptance=False):
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
            if owner_acceptance and len(calls)==1:time.sleep(5)
            content=({'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake web app'}
                     if 'is_web_app' in json.dumps(payload) else
                     {'question':'What does a booking look like for your customers?','covered':[],'thin':[],'skipped':[],'complete':False})
            message={'role':'assistant','content':json.dumps(content)};finish='stop'
            if payload.get('tools'):
                tool=payload['tools'][0]['function']['name']
                message={'role':'assistant','content':json.dumps(content),'tool_calls':[{'id':'fake-tool','type':'function','function':{'name':tool,'arguments':json.dumps({'payload':content})}}]};finish='tool_calls'
            body=json.dumps({'id':'fake','object':'chat.completion','model':payload['model'],'choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11,**({'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10} if payload['model'].startswith('deepseek') else {})}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    vendor=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=vendor.serve_forever,daemon=True).start()
    import os
    browser_cache=os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(Path.home()/'.cache/ms-playwright')
    env=pg_runtime_environment()
    if owner_acceptance:monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',browser_cache)
    selected_route='ollama';selected_model=MODEL
    if paid:
        # Vendor/config fixtures replace only external pricing/balance/HTTP,
        # while real ProductionGateways and child/accounting remain installed.
        import datetime
        from test_production_gateway import row,snapshot,CEILING
        from delivery_api import production_gateway
        from delivery_api.child_paid_runtime import PaidChildRuntime
        selected_route='deepseek';selected_model='deepseek-flash'
        snap=snapshot();snap['model']=selected_model
        paidrow=row(model=selected_model,provider_snapshot=snap)
        ceiling=dict(CEILING,model=selected_model)
        env.update({production_gateway.PRICES_ENV:json.dumps([paidrow]),production_gateway.CEILING_ENV:json.dumps(ceiling)})
        original=production_gateway.ProductionGateways.__init__
        def init(self,*args,**kw):
            kw['vendor_fetcher']=lambda *a:None
            original(self,*args,**kw)
            self.verify_vendor=lambda:None
            self.provider_balance=lambda:{'response':{'is_available':True,'balance_infos':[{'currency':'USD','total_balance':'1000'}]},'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        monkeypatch.setattr(production_gateway.ProductionGateways,'__init__',init)
        monkeypatch.setattr(PaidChildRuntime,'transport',lambda self,p,m,local:(f'http://127.0.0.1:{vendor.server_port}/v1/chat/completions',{}))
    if paid:
        env.update(REQUI_PIN_STORE='1',REQUI_PIN_PROVIDERS='deepseek')
        from delivery_api import model_pins
        monkeypatch.setattr(model_pins,'refresh',lambda value:{'payload':value,'changed':False,'warning':None})
        monkeypatch.setattr(model_pins,'pin_verifier',lambda value:lambda:True)
    if not paid:env.update(local_env(MODEL))
    env.update(DELIVERY_CLOUD_EXECUTION='1',DELIVERY_CHILD_EXECUTION='local',INTERVIEW_ENGINE_TOKEN='acceptance-not-a-live-secret-0000',OLLAMA_BASE_URL=f'http://127.0.0.1:{vendor.server_port}/v1',INTERVIEW_ENGINE_OLLAMA_MODEL=MODEL)
    for key,value in env.items():monkeypatch.setenv(key,value)
    if paid:
        for key,value in {'DELIVERY_CHILD_EXECUTION':'attended-paid','DELIVERY_PAID_CHILD_PREVIEW':'1','DELIVERY_PREVIEW':'1','DELIVERY_PUBLIC_ORIGIN':'http://127.0.0.1:8001','DEEPSEEK_API_KEY':'acceptance-fake-no-real-key'}.items():monkeypatch.setenv(key,value)
        env.update(DELIVERY_PAID_CHILD_PREVIEW='1')
    else:
        monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
        # Same default launcher topology: local can boot even when the paid route
        # is offered but has no key, reviewed pin, balance or expense policy.
        from delivery_api import model_pins
        env.update(REQUI_PIN_STORE='1',REQUI_PIN_PROVIDERS='deepseek,ollama',
                   DELIVERY_CHILD_EXECUTION='attended-paid',DELIVERY_PAID_CHILD_PREVIEW='1',DELIVERY_PREVIEW='1',DELIVERY_PUBLIC_ORIGIN='http://127.0.0.1:8001')
        for key,value in env.items():monkeypatch.setenv(key,value)

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
    report=preflight.run(dict(__import__('os').environ))
    assert not [c for c in report.checks if c.level=='refuse' and c.status!='pass'],report
    with pg_workspace_factory() as workspace:
        with workspace.admin() as c:
            c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,%s,1000000000,0)',(workspace.scope.owner_id,100000000000 if paid else 0))
            if paid:c.execute("INSERT INTO expense_guard_policy VALUES(true,'acceptance',1000000000000,1000000000000,1000000000000,3) ON CONFLICT DO NOTHING")
            if paid:
                c.execute('DELETE FROM operator_expense_policy_head');c.execute('DELETE FROM operator_expense_policy_revisions')
                c.execute("INSERT INTO operator_expense_policy_revisions(revision,limit_units,units_per_usd,operator_subject,reason) VALUES('http-policy',1000000000000,1000000000000,%s,'fake acceptance policy')",(workspace.scope.owner_id,))
                c.execute("INSERT INTO operator_expense_policy_head VALUES(true,'http-policy')")
            c.execute('INSERT INTO admission_limits VALUES(%s,%s,86400,NULL,NULL,NULL,NULL,60,NULL,3)',(workspace.scope.owner_id,'acceptance'))
        if paid:
            from delivery_api import model_pins
            value={'row':paidrow,'context':ceiling};rev=model_pins.revision(value)
            with workspace.admin() as c:
                c.execute('DELETE FROM model_pin_heads');c.execute('DELETE FROM model_pin_revisions')
                c.execute('INSERT INTO model_pin_revisions(revision,provider,model,payload,fetched_at,operator_subject,reason) VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s)',(rev,'deepseek',selected_model,json.dumps(value),snap['fetched_at'],workspace.scope.owner_id,'HTTP acceptance reviewed pin'))
                c.execute('INSERT INTO model_pin_heads(provider,model,revision,is_default) VALUES(%s,%s,%s,true)',('deepseek',selected_model,rev))
        if not paid:
            value={'row':json.loads(env['VMODEL_REVIEWED_MODEL_PRICES'])[0],
                   'context':json.loads(env['VMODEL_REVIEWED_CONTEXT_CEILING'])}
            rev=model_pins.revision(value)
            with workspace.admin() as c:
                c.execute('DELETE FROM model_pin_heads');c.execute('DELETE FROM model_pin_revisions')
                c.execute('INSERT INTO model_pin_revisions(revision,provider,model,payload,fetched_at,operator_subject,reason) VALUES(%s,%s,%s,%s::jsonb,now(),%s,%s)',(rev,'ollama',MODEL,json.dumps(value),workspace.scope.owner_id,'zero-cost local acceptance reviewed pin'))
                c.execute('INSERT INTO model_pin_heads(provider,model,revision,is_default) VALUES(%s,%s,%s,true)',('ollama',MODEL,rev))
        if owner_acceptance:
            with workspace.admin() as c:
                c.execute('DELETE FROM operator_principals')
                c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'owner-acceptance',ARRAY['sessions.operate'],true)",(workspace.scope.owner_id,))
        middleware=pg_task_app(workspace,lifespan=True)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(middleware,host='127.0.0.1',port=port,lifespan='on',log_level='warning',access_log=False))
        import asyncio
        loops=[]
        async def serve():
            loops.append(asyncio.get_running_loop())
            await server.serve()
        thread=threading.Thread(target=lambda:asyncio.run(serve()));thread.start()
        try:
            for _ in range(100):
                if server.started:break
                assert thread.is_alive(),'actual application startup failed'
                time.sleep(.05)
            assert server.started and cloud_runtime.installed(),cloud_runtime.failure()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers=workspace.headers,timeout=20) as client:
                if owner_acceptance:
                    spec=importlib.util.spec_from_file_location('owner_acceptance',Path(__file__).parents[1]/'scripts/owner/deepseek-acceptance.py')
                    owner_script=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner_script)
                    api=owner_script.API(client)
                    # Use real task/API/producers/PG and only replace the operator
                    # aggregate read (separately tested under its restricted role).
                    def owner_api(method,path,body=None,**kwargs):
                        if path.startswith('/api/operator/task-usage?'):
                            return {'complete':True,'attempts':len(calls)}
                        return api(method,path,body,**kwargs)
                    seen=[];observed={'live':False,'cost':False}
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as pw:
                        browser=pw.chromium.launch(headless=True)
                        context=browser.new_context(extra_http_headers=workspace.headers)
                        page=context.new_page()
                        def observe(task):
                            seen.append(task)
                            url=f'http://127.0.0.1:{port}/tasks/'+task['task_id']+'/view'
                            if page.url!=url:page.goto(url)
                            if task['control_snapshot']['mode']=='working' and any(e.get('payload',{}).get('source')=='interview' for e in task.get('events',[])):
                                line=page.locator('.phase-list-primary > .phase').first.locator('.phase-current-progress')
                                if line.is_visible() and line.inner_text().strip():
                                    observed['live']=True
                                    from test_artifacts import evidence_path
                                    page.screenshot(path=evidence_path('owner-acceptance-live-start.png'),full_page=True)
                            header=page.locator('#sessioncost')
                            if header.is_visible() and header.inner_text().strip():observed['cost']=True
                        tid,task,cost,usage=owner_script.journey(owner_api,observe=observe)
                        page.reload()
                        from playwright.sync_api import expect
                        expect(page.locator('#currentquestion p')).to_contain_text(task['control_snapshot']['prompt'],timeout=15000)
                        browser.close()
                    assert observed['live'] and observed['cost'],observed
                    assert cost['evidence']['complete'] and usage['provider_expense']['complete']
                    assert any(e.get('kind')=='progress' for t in seen for e in t.get('events',[]))
                    out=api('POST','/api/operations/test-tasks/'+tid+'/purge',{'confirm':True})
                    assert out['purged'] and out['accounting_retained']
                    return
                body={'title':'Acceptance recycled name','brief':'A booking app for a small salon'}
                first=client.post('/api/tasks',json=body);assert first.status_code==201,first.text
                tid=first.json()['task_id']
                deleted=client.request('DELETE','/api/tasks/'+tid,json={'confirm':True});assert deleted.status_code==200,deleted.text
                created=client.post('/api/tasks',json=body);assert created.status_code==201,created.text
                tid=created.json()['task_id']
                start=client.post(f'/api/tasks/{tid}/start',json={'model_route':selected_route});assert start.status_code==202,start.text
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
                    assert rows and all(r[0]=='settled' and r[3] for r in rows),rows
                    if not paid:assert all(r[1]==r[2]==0 for r in rows)
                    else:assert all(r[1]>0 and r[2]>0 for r in rows)
                    balance=c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()[0]
                    assert balance<100000000000 if paid else balance==0
        finally:
            def stop_streams():
                for stop in tuple(module._stream_shutdown_events):stop.set()
            if loops:loops[0].call_soon_threadsafe(stop_streams)
            server.should_exit=True;thread.join(10);vendor.shutdown();vendor.server_close()
            assert not thread.is_alive()
            # Global test-only policy must not leak into later guard suites.
            if paid:
                with workspace.admin() as c:
                    c.execute("DELETE FROM expense_guard_policy WHERE policy_version='acceptance'")
