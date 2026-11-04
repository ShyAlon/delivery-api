import asyncio,json,uuid,sys
import pytest
from delivery_api.child_generator import ChildGeneratorEngine,manifest
from delivery_api.inprocess_engine import VirtualProcess
from delivery_api.tenancy import tenant_context,session_root
from test_postgres_store import A,DSN


@pytest.mark.parametrize('stage',['needs','srs','publication-recovery','paid-needs','installed-paid-srs','open-needs'])
def test_real_generator_cli_child_produces_needs_and_reaps(store,tmp_path,monkeypatch,stage):
    pytest.importorskip('spec_generator')
    from delivery_api import config
    from vmodel_contracts import CONTRACT_VERSION
    open_chain=stage=='open-needs'
    if open_chain:stage='needs'
    installed=stage=='installed-paid-srs'
    paid=stage in ('paid-needs','installed-paid-srs')
    if paid:stage='srs' if installed else 'needs'
    publishing=stage=='publication-recovery'
    stage='needs' if publishing else stage
    monkeypatch.setattr(config,'ARTIFACT_PUBLISH',publishing,raising=False)
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    with tenant_context(A):
        tid=store.create_task('generator child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
        root=session_root(tmp_path,A,sid);interview=root/'interview';interview.mkdir(parents=True);out=root/'spec'
        intent={'contract_version':CONTRACT_VERSION,'session_id':sid,'problem_statement':'Record task evidence','constraints':[],'assumptions':[],'glossary':{},'target_users':['People'],'core_value_proposition':'Task evidence','stakeholder_needs':[{'need_id':'SN-1','statement':'People need recorded evidence','kind':'functional','source_turns':[0]}],'in_scope':['Record evidence'],'out_of_scope':[],'source_turns':{}}
        ip=interview/'intent_model.json'
        baseline={'contract_version':CONTRACT_VERSION,'baseline_id':'greenfield','system_name':'Test','mode':'greenfield','repositories':[]}
        bp=out/'system-baseline.json'
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':json.dumps(intent).encode()},{'phase_key':'baseline','name':'system-baseline.json','data':json.dumps(baseline).encode()}],writer_instance='test-only')
        job=store.enqueue_worker(tid,'phase:'+stage,execution_options={'model_route':'ollama:fake'})
        from http.server import HTTPServer,BaseHTTPRequestHandler
        import threading
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                from strict_provider import read_request
                payload=read_request(self);calls.append(payload)
                content=json.dumps({'purpose':'Record task evidence','scope_summary':'Record evidence for people'}) if 'purpose, scope_summary' in json.dumps(payload) else json.dumps({'title':'Record evidence','statement':'The system shall record task evidence for people.','priority':'must','acceptance':['Task evidence is recorded.']})
                if payload.get('stream'):
                    events=[{'id':'fake','model':'fake','choices':[{'index':0,'delta':{'role':'assistant','content':content},'finish_reason':None}]},
                        {'id':'fake','model':'fake','choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}]
                    if payload.get('tools'):
                        tool=payload['tools'][0]['function']['name']
                        events[0]['choices'][0]['delta']={'role':'assistant','tool_calls':[{'index':0,'id':'fake-tool','type':'function','function':{'name':tool,'arguments':json.dumps({'payload':json.loads(content)})}}]}
                        events[1]['choices'][0]['finish_reason']='tool_calls'
                    body=b''.join(b'data: ' +json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n';ct='text/event-stream'
                else:
                    body=json.dumps({'id':'fake','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}).encode();ct='application/json'
                self.send_response(200);self.send_header('Content-Type',ct);self.end_headers();self.wfile.write(body)
            def log_message(self,*a):pass
        server=HTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        class Gateway:
            retail_rates=None
            def __init__(self,job):self.store=store;self.job_id=job;self.states={}
            def reserve_serialized(self,**kw):key=uuid.uuid4().hex;self.states[key]='reserved';return key
            def start(self,a):self.states[a]='started'
            def settle_raw(self,a,u):self.states[a]='settled'
            def uncertain(self,a):self.states[a]='uncertain'
            def _attempt(self,a):return {'state':self.states[a]}
        from contextlib import ExitStack
        stack=ExitStack();factory=lambda cap,job:Gateway(job);paid_runtime=None;route='ollama:fake'
        if paid:
            import psycopg
            from paid_child_fixture import paid_child
            factory,paid_runtime=stack.enter_context(paid_child(store,f'http://127.0.0.1:{server.server_port}/v1/chat/completions'))
            route='deepseek:deepseek-flash'
            with psycopg.connect(DSN) as conn:conn.execute("UPDATE worker_jobs SET execution_options=%s::jsonb WHERE job_id=%s",(json.dumps({'model_route':route}),job))
        engine=ChildGeneratorEngine(tmp_path,gateway_factory=factory,paid_runtime=paid_runtime,dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
            command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(__import__('pathlib').Path(__file__).parents[2]/'interview-engine/src'),str(__import__('pathlib').Path(__file__).parents[2]/'spec-generator/src')])
        if installed:
            from delivery_api import cloud_runtime,inprocess_engine
            from delivery_api.production_gateway import ProductionGateways
            cloud_runtime.uninstall()
            class Factory:
                models=paid_runtime.models;verify_vendor=None
                def __call__(self,*args):return factory(*args)
                def refresh_launch(self):pass
                def close(self):pass
            monkeypatch.setattr('delivery_api.model_pins.DurableGateways',lambda *a:Factory())
            from test_production_gateway import env as gateway_env
            cloud_runtime.install(store,gateway_env(DELIVERY_CHILD_EXECUTION='local',DELIVERY_LOCAL_PREVIEW='1',DELIVERY_DATABASE_URL=engine.dsn,VMODEL_ARTIFACTS_DIR=str(tmp_path)),lambda c:None,paid_runtime=paid_runtime)
            installed_engine=inprocess_engine.current();installed_engine.command=engine.command;installed_engine.endpoint=engine.endpoint;engine=installed_engine
        argv=[sys.executable,'-m','spec_generator.cli',str(ip),'--system-baseline',str(bp),'--out',str(out),'--to',stage]
        async def go():
            proc=VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_NEEDS':route})
            assert await engine._worker(store,proc).execute(job,engine._produce(proc))
            assert await proc.wait()==0
            return await proc.stdout.read()
        try:
            if publishing:
                import delivery_api.artifact_publication as publication
                real=publication.publish_document_revision
                def fail(*a,**kw):raise RuntimeError('injected publication outage')
                monkeypatch.setattr(publication,'publish_document_revision',fail)
                with pytest.raises(RuntimeError,match='publication outage'):asyncio.run(go())
                assert store.producer_result(job) is not None
                monkeypatch.setattr(publication,'publish_document_revision',real)
                # Recover using a NEW adapter object, no child may be started.
                recovered=ChildGeneratorEngine(tmp_path,gateway_factory=engine.gateway_factory,dsn=engine.dsn,endpoint=engine.endpoint)
                def forbidden(*a):raise AssertionError('publication recovery cannot spawn')
                recovered._produce=forbidden
                store.reauthorize_worker(job)
                async def recover():
                    proc=VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_NEEDS':'ollama:fake'})
                    assert await recovered._worker(store,proc).execute(job,lambda *a:(_ for _ in ()).throw(AssertionError('rerun')))
                    assert await asyncio.wait_for(proc.wait(),2)==0
                asyncio.run(recover());output=b'Stopped after stage'
                assert store.producer_result(job)['published_at'] is not None
            elif open_chain:
                from delivery_api.runner import ProcessRunner
                from delivery_api import inprocess_engine
                from delivery_api.orchestrator import ExecutionMode
                runner=ProcessRunner(store);seen=[]
                store.set_model_default(tid,route);store.set_gate_open(tid,'gate-needs',True)
                monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path))
                class Orch:
                    async def execute_phase(self,task_id,phase,**options):
                        seen.append(phase)
                        cmd=[*argv];cmd[-1]=phase
                        code=await runner.run(task_id,cmd,cwd=tmp_path,phase=phase,infer_phase=False,
                            env={'VMODEL_ARTIFACTS_DIR':str(root.parent),'VMODEL_PROGRESS_TASK_ID':tid,
                                 'VMODEL_PROGRESS_SESSION_ID':sid,'SPEC_GENERATOR_MODEL_'+phase.upper():route})
                        assert code==0
                assert asyncio.run(engine.dispatch(store,Orch(),tid,'needs',job,{}))
                assert seen==['needs','srs'] and not runner.processes and inprocess_engine.take(tid) is None
                assert store.task(tid)['state']=='waiting_gate'
                assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='needs')['state']=='bypassed'
                assert next(g for g in store.task(tid)['gates'] if g['after_phase']=='srs')['state']=='waiting'
                output=b'Stopped after stage'
            else:output=asyncio.run(go())
        finally:
            server.shutdown();server.server_close()
            if paid:
                with store._postgres.transaction() as conn:
                    rows=conn.execute("SELECT state,deterministic_ms FROM retail_compute_segments WHERE job_id=%s",(job,)).fetchall()
                    assert len(rows)==1 and rows[0]['state']=='settled' and rows[0]['deterministic_ms']>0
                    assert conn.execute("SELECT count(*) AS n FROM retail_contributions WHERE job_id=%s AND kind='tokens'",(job,)).fetchone()['n']==len(calls)
            if installed:cloud_runtime.uninstall()
            stack.close()
        assert not (out/'checkpoints.sqlite').exists(), 'child generator must persist checkpoints only in PG'
        assert not (out/'needs.json').exists() and b'Stopped after stage' in output
        with store._postgres.transaction() as c:
            needs=c.execute('SELECT payload FROM generator_documents WHERE execution_session_id=%s AND document_key=%s',(sid,'spec/needs.json')).fetchone()
        assert json.loads(bytes(needs['payload']))['needs']
        assert store.worker_job(job)['state']=='done' and engine._quiescent
        if stage=='srs':
            assert calls and not (out/'srs.json').exists()
            with store._postgres.transaction() as c:
                assert json.loads(bytes(c.execute('SELECT payload FROM generator_documents WHERE execution_session_id=%s AND document_key=%s',(sid,'spec/srs.json')).fetchone()['payload']))['functional']


def test_manifest_rejects_foreign_or_changed_inputs(tmp_path):
    from types import SimpleNamespace
    root=tmp_path/'owned';root.mkdir();outside=tmp_path/'foreign.json';outside.write_text('{}')
    retained={'intent.json':b'{}'};documents=SimpleNamespace(read=retained.get)
    with pytest.raises(PermissionError):manifest([str(outside),'--out',str(root/'spec')],root,documents)
    owned=root/'intent.json'
    a,digest=manifest([str(owned),'--out',str(root/'spec')],root,documents)
    owned.write_text('{"host":"must not override PG"}')
    assert manifest([str(owned),'--out',str(root/'spec')],root,documents)[1]==digest
    retained['intent.json']=b'{"changed":true}';owned.unlink()
    assert manifest([str(owned),'--out',str(root/'spec')],root,documents)[1]!=digest
    retained.clear()
    with pytest.raises(PermissionError,match='missing'):manifest([str(owned),'--out',str(root/'spec')],root,documents)


def test_manifest_cannot_accept_file_only_storage(tmp_path):
    owned=tmp_path/'intent.json';owned.write_text('{}')
    with pytest.raises(PermissionError,match='documents required'):
        manifest([str(owned),'--out',str(tmp_path/'spec')],tmp_path)


@pytest.mark.parametrize('paid',[False,True])
def test_durable_gate_answer_survives_parent_crash_without_reasking(store,tmp_path,monkeypatch,paid):
    from delivery_api import config
    monkeypatch.setattr(config,'ARTIFACT_PUBLISH',False,raising=False);monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    with tenant_context(A):
        tid=store.create_task('gate restart '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
        root=session_root(tmp_path,A,sid);root.mkdir(parents=True);ip=root/'interview/intent_model.json'
        store.publish_task_revision_bytes(tid,[{'phase_key':'intent','name':'intent_model.json','data':b'{}'}],writer_instance='test-only')
        route='deepseek:deepseek-flash' if paid else 'ollama:fake'
        job=store.enqueue_worker(tid,'phase:needs',execution_options={'model_route':route})
        class Gateway:
            retail_rates=None
            def __init__(self,job):self.job_id=job;self.store=store
        from pathlib import Path
        command=[sys.executable,str(Path(__file__).parent/'fixtures/child_generator_gate_fixture.py'),
            str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
        from contextlib import ExitStack
        stack=ExitStack();factory=lambda cap,job:Gateway(job);runtime=None
        if paid:
            from paid_child_fixture import paid_child
            factory,runtime=stack.enter_context(paid_child(store,'http://127.0.0.1:1'))
        def engine():return ChildGeneratorEngine(tmp_path,gateway_factory=factory,paid_runtime=runtime,dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint='http://127.0.0.1:1',command=command)
        argv=[sys.executable,'-m','spec_generator.cli',str(ip),'--out',str(root/'spec')];asked=[]
        async def go(e):
            proc=VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_NEEDS':route})
            def decide(d):asked.append(d);return 'a'
            proc.decide=decide
            assert await e._worker(store,proc).execute(job,e._produce(proc))
        try:
            original=store.retain_decision_review
            def crash_after_answer(task,wait,revision,payload):
                original(task,wait,revision,payload)
                if revision.startswith('answer:gate:'):raise RuntimeError('parent crashed after durable answer')
            monkeypatch.setattr(store,'retain_decision_review',crash_after_answer)
            with pytest.raises(RuntimeError,match='parent crashed'):asyncio.run(go(engine()))
            monkeypatch.setattr(store,'retain_decision_review',original);store.reauthorize_worker(job)
            asyncio.run(go(engine()))
            assert len(asked)==1 and not (root/'spec/needs.json').exists()
            with store._postgres.transaction() as c:
                assert json.loads(bytes(c.execute('SELECT payload FROM generator_documents WHERE execution_session_id=%s AND document_key=%s',(sid,'spec/needs.json')).fetchone()['payload']))['accepted'] is True
            assert store.worker_job(job)['state']=='done'
            if paid:
                with store._postgres.transaction() as conn:
                    rows=conn.execute('SELECT state FROM retail_compute_segments WHERE job_id=%s',(job,)).fetchall()
                    assert len(rows)==3 and all(row['state']=='settled' for row in rows)
                    assert conn.execute("SELECT count(*) AS n FROM retail_contributions WHERE job_id=%s AND kind='compute'",(job,)).fetchone()['n']==3
                    assert conn.execute('SELECT count(*) AS n FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()['n']==0
    
        finally:stack.close()


def test_items_phase_uses_generator_stage_not_delivery_specs_alias():
    from delivery_api.phases import PHASE_BY_KEY
    assert PHASE_BY_KEY['specs'].generator_stage=='items'
    # The typed request must never emit the delivery alias, which the strict
    # neutral contract intentionally rejects.
    import inspect
    assert "'phase':generator_stage" in inspect.getsource(ChildGeneratorEngine._produce)


@pytest.mark.parametrize('tail',[['--to','tests'],['--to','validation','--from','srs'],[]])
def test_final_review_cannot_widen_generator_phase(tmp_path,tail):
    with pytest.raises(PermissionError,match='bounded validation'):
        manifest([str(tmp_path/'intent.json'),'--out',str(tmp_path/'spec'),'--final-review',*tail],tmp_path)
