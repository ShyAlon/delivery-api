"""One generated task, installed paid adapters, fake HTTP only, retained download."""
import asyncio,hashlib,io,json,sys,threading,uuid,zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from fastapi.testclient import TestClient
from delivery_api import cloud_runtime,inprocess_engine,config
from delivery_api.tenancy import tenant_context,session_root
from test_postgres_store import A,DSN
from test_production_gateway import env
from paid_child_fixture import paid_child, InstalledGatewayFactory, installed_child_command
from paid_http_fake import answer


def test_single_installed_paid_task_through_final_download(store,tmp_path,monkeypatch,pg_task_app):
    monkeypatch.setenv('REQUI_RE_DIST','1')  # exercise installed producer export, not sibling checkout venv
    calls=[];errors=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload)
            try:content=answer(payload)
            except Exception as exc:errors.append(repr(exc));content={}
            message={'role':'assistant','content':json.dumps(content)};finish='stop'
            if payload.get('tools'):
                tool=payload['tools'][0]['function']['name']
                message['tool_calls']=[{'id':'fake-tool','type':'function','function':{'name':tool,'arguments':json.dumps({'payload':content})}}];finish='tool_calls'
            usage={'prompt_tokens':10,'completion_tokens':10,'total_tokens':20,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}
            if payload.get('stream'):
                delta={k:v for k,v in message.items() if k!='tool_calls'}
                if 'tool_calls' in message:delta['tool_calls']=[{'index':0,**message['tool_calls'][0]}]
                events=[{'choices':[{'index':0,'delta':delta,'finish_reason':None}]},{'choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':usage}]
                body=b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n';ct='text/event-stream'
            else:body=json.dumps({'id':'fake','model':'deepseek-flash','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':usage}).encode();ct='application/json'
            self.send_response(200);self.send_header('Content-Type',ct);self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    command=installed_child_command()
    from delivery_api.child_supervisor import ChildSupervisor
    original=ChildSupervisor.__init__
    def supervisor(self,*a,**kw):kw['command']=command;original(self,**kw)
    monkeypatch.setattr(ChildSupervisor,'__init__',supervisor)
    monkeypatch.setattr(config,'ARTIFACT_PUBLISH',True,raising=False);monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    cloud_runtime.uninstall()
    try:
        with paid_child(store,endpoint) as (factory,runtime),tenant_context(A):
            monkeypatch.setattr('delivery_api.model_pins.DurableGateways',lambda *a:InstalledGatewayFactory(factory,runtime))
            chosen=[]
            cloud_runtime.install(store,env(DELIVERY_CHILD_EXECUTION='local',DELIVERY_LOCAL_PREVIEW='1',DELIVERY_DATABASE_URL=DSN.replace('user=sandbox','user=delivery_app'),VMODEL_ARTIFACTS_DIR=str(tmp_path)),chosen.append,paid_runtime=runtime)
            engine=inprocess_engine.current();engine.command=command;client=chosen[0]
            tid=store.create_task('all-phase '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            from vmodel_contracts.interactive import StartSessionRequest,InputResponse
            snapshot=client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='A SaaS web app for founders who need specs',model_route='deepseek',initial_model='deepseek-flash')).snapshot
            submitted=0
            for n in range(12):
                if not snapshot.open_input:break
                item=snapshot.open_input
                value='Founders need specs. Visual style is blue. On error show retry. Empty first-use shows a welcome. Accessibility includes keyboard navigation. No integrations; billing is out of scope.'
                if item.kind.name=='CHOICE':value=item.options[0].value
                if item.kind.name=='APPROVAL':value=1
                snapshot=client.submit(sid,InputResponse(input_id=item.input_id,expected_sequence=snapshot.sequence,value=value,rationale='First option fits this test.',actor_id=A.owner_id)).snapshot
                submitted+=1
            assert not snapshot.open_input and not errors,errors
            assert client.supervisor.last_result.reaped
            # Read generated interview documents directly from durable checkpoint publication.
            root=session_root(tmp_path,A,sid);out=root/'spec'
            baseline={'contract_version':__import__('vmodel_contracts').CONTRACT_VERSION,'baseline_id':'greenfield','system_name':'Test','mode':'greenfield','repositories':[]}
            store.publish_task_revision_bytes(tid,[{'phase_key':'baseline','name':'system-baseline.json','data':json.dumps(baseline).encode()}],writer_instance='explicit-test-baseline')
            pinned={}
            for phase,stage in [('needs','needs'),('srs','srs'),('architecture','architecture'),('specs','items'),('tests','tests'),('validation','validation')]:
                job=store.enqueue_worker(tid,'phase:'+phase,execution_options={'model_route':'deepseek:deepseek-flash'})
                argv=[sys.executable,'-m','spec_generator.cli',str(root/'interview/intent_model.json'),'--conops',str(root/'interview/conops.json'),'--system-baseline',str(out/'system-baseline.json'),'--out',str(out),'--to',stage]+(['--final-review'] if phase=='validation' else [])
                async def go():
                    proc=inprocess_engine.VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_'+s.upper():'deepseek:deepseek-flash' for s in ['needs','srs','architecture','items','tests','validation','review']})
                    proc.decide=lambda d:'a'
                    assert await engine._worker(store,proc).execute(job,engine._produce(proc))
                    output=await proc.stdout.read();assert b'Stopped after stage' in output,output.decode()[-3000:]
                    assert await proc.wait()==0
                asyncio.run(go());assert engine._quiescent and not errors,errors
                assert store.worker_job(job)['state']=='done'
                current={(r['phase_key'],r['name']):r['sha256'] for r in store.manifest_snapshot(tid)['rows']}
                assert all(current.get(key)==digest for key,digest in pinned.items())
                pinned={key:digest for key,digest in current.items() if key[0] not in ('handoff',)}
                store.update_phase(tid,phase,'approved')
            for phase in ['intent','conops']:store.update_phase(tid,phase,'approved')
            with store._postgres.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM retail_contributions r JOIN worker_jobs w USING(job_id) WHERE w.task_id=%s AND r.kind='tokens'",(tid,)).fetchone()['n']==len(calls)
                segments=conn.execute("SELECT r.state,r.deterministic_ms FROM retail_compute_segments r JOIN worker_jobs w USING(job_id) WHERE w.task_id=%s",(tid,)).fetchall()
                # Default answer-v1 replaces per-answer compute segments with
                # final receipts. Start plus six generator phases keep segments.
                assert len(segments)==7 and all(r['state']=='settled' and r['deterministic_ms']>0 for r in segments)
                receipts=conn.execute("SELECT state,usage_manifest FROM answer_charge_receipts WHERE session_id=%s",(sid,)).fetchall()
                assert len(receipts)==submitted and submitted>=2
                assert all(r['state']=='settled' and float(r['usage_manifest']['deterministic_ms'])>0 for r in receipts)
            snapshot=store.manifest_snapshot(tid)
            names={(r['phase_key'],r['name']) for r in snapshot['rows']}
            assert {('intent','intent_model.json'),('conops','conops.json'),('needs','needs.json'),('srs','srs.json'),('architecture','architecture.json'),('specs','items.json'),('tests','tests.json'),('validation','validation.json'),('handoff','findings-ledger.json')} <= names
            assert not list(tmp_path.rglob('intent_model.json')) and not list(tmp_path.rglob('checkpoints.sqlite'))
            # Production authenticated routes must export this generated task, never fixture artifacts.
            from pg_workspace import Workspace
            ws=Workspace(DSN,store,A);http=TestClient(pg_task_app(ws),headers=ws.headers)
            before=len(calls)
            response=http.post(f'/api/tasks/{tid}/packages',json={});assert response.status_code==200,response.text
            downloaded=http.get(response.json()['url']);assert downloaded.status_code==200
            assert len(calls)==before,'export must not call a provider'
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:
                manifest=json.loads(z.read('package.json'));assert manifest['task_id']==tid
                assert 'spec/validation.json' in z.namelist()
                for name,digest in manifest['sha256'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
    finally:
        cloud_runtime.uninstall();server.shutdown();server.server_close()
