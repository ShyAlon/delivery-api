"""Unactivated trusted installer seam; real adapters, disposable PG/fake HTTP."""
import uuid
import pytest
from delivery_api import cloud_runtime,inprocess_engine
from delivery_api.child_paid_runtime import PaidChildRuntime
from delivery_api.tenancy import tenant_context
from test_postgres_store import store,A,DSN
from test_production_gateway import env,MODEL


@pytest.fixture(autouse=True)
def cleanup():
    cloud_runtime.uninstall();yield;cloud_runtime.uninstall()


def test_installer_rejects_missing_pins_before_any_dispatch(store,tmp_path):
    chosen=[]
    with pytest.raises(ValueError):
        cloud_runtime.install(store,{'DELIVERY_DATABASE_URL':DSN,'VMODEL_ARTIFACTS_DIR':str(tmp_path)},chosen.append)
    assert chosen==[] and not cloud_runtime.installed()


@pytest.mark.parametrize('answer_mode',['default','legacy','answer-v1'])
def test_installed_adapters_run_real_paid_child_with_fake_provider(store,tmp_path,monkeypatch,answer_mode):
    import asyncio,json,sys,threading
    from pathlib import Path
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from paid_child_fixture import paid_child, InstalledGatewayFactory, installed_child_command
    from delivery_api.child_supervisor import ChildSupervisor
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            from strict_provider import read_request
            payload=read_request(self);calls.append(payload)
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
    command=installed_child_command()
    original=ChildSupervisor.__init__
    def supervisor(self,*args,**kwargs):kwargs['command']=command;original(self,**kwargs)
    monkeypatch.setattr(ChildSupervisor,'__init__',supervisor)
    try:
        with paid_child(store,endpoint) as (factory,runtime),tenant_context(A):
            monkeypatch.setattr('delivery_api.model_pins.DurableGateways',lambda *a:InstalledGatewayFactory(factory,runtime))
            chosen=[]
            config=env(DELIVERY_CHILD_EXECUTION='local',DELIVERY_LOCAL_PREVIEW='1',DELIVERY_DATABASE_URL=DSN.replace('user=sandbox','user=delivery_app'),VMODEL_ARTIFACTS_DIR=str(tmp_path))
            if answer_mode!='default':config['DELIVERY_ANSWER_ACCOUNTING_MODE']=answer_mode
            # Match startup: installation runs with no authenticated tenant.
            from delivery_api.tenancy import _scope
            token=_scope.set(None)
            try:cloud_runtime.install(store,config,chosen.append,paid_runtime=runtime)
            finally:_scope.reset(token)
            assert (chosen[0].answer_accounting is not None)==(answer_mode!='legacy')
            tid=store.create_task('installed child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            from vmodel_contracts.interactive import StartSessionRequest
            result=chosen[0].start(StartSessionRequest(session_id=sid,task_id=tid,idea='A booking app',model_route='deepseek',initial_model='deepseek-flash'))
            assert result.snapshot.open_input is not None and chosen[0].supervisor.last_result.reaped and calls
            with store._postgres.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM retail_compute_segments WHERE state='settled'").fetchone()['n']==1
                assert conn.execute("SELECT count(*) AS n FROM retail_contributions r JOIN worker_jobs w USING(job_id) WHERE r.kind='tokens' AND w.task_id=%s",(tid,)).fetchone()['n']==len(calls)
            from vmodel_contracts.interactive import InputResponse
            answer=result.snapshot.open_input
            chosen[0].submit(sid,InputResponse(input_id=answer.input_id,value='Customers choose a service and time',actor_id='fixture',expected_sequence=result.snapshot.sequence))
            with store._postgres.transaction() as conn:
                receipts=conn.execute("SELECT state FROM answer_charge_receipts WHERE session_id=%s",(sid,)).fetchall()
                assert len(receipts)==(1 if answer_mode!='legacy' else 0)
                if receipts:assert receipts[0]['state']=='settled'

    finally:server.shutdown();server.server_close()
