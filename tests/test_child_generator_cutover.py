import asyncio,json,uuid,sys
import pytest
from delivery_api.child_generator import ChildGeneratorEngine,manifest
from delivery_api.inprocess_engine import VirtualProcess
from delivery_api.tenancy import tenant_context,session_root
from test_postgres_store import A,DSN


@pytest.mark.parametrize('stage',['needs','srs','publication-recovery'])
def test_real_generator_cli_child_produces_needs_and_reaps(store,tmp_path,monkeypatch,stage):
    pytest.importorskip('spec_generator')
    from delivery_api import config
    from vmodel_contracts import CONTRACT_VERSION
    publishing=stage=='publication-recovery'
    stage='needs' if publishing else stage
    monkeypatch.setattr(config,'ARTIFACT_PUBLISH',publishing)
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    with tenant_context(A):
        tid=store.create_task('generator child '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
        root=session_root(tmp_path,A,sid);interview=root/'interview';interview.mkdir(parents=True);out=root/'spec'
        intent={'contract_version':CONTRACT_VERSION,'session_id':sid,'problem_statement':'Record task evidence','constraints':[],'assumptions':[],'glossary':{},'target_users':['People'],'core_value_proposition':'Task evidence','stakeholder_needs':[{'need_id':'SN-1','statement':'People need recorded evidence','kind':'functional','source_turns':[0]}],'in_scope':['Record evidence'],'out_of_scope':[],'source_turns':{}}
        ip=interview/'intent_model.json';ip.write_text(json.dumps(intent))
        bp=root/'baseline.json';bp.write_text(json.dumps({'contract_version':CONTRACT_VERSION,'baseline_id':'greenfield','system_name':'Test','mode':'greenfield','repositories':[]}))
        job=store.enqueue_worker(tid,'phase:'+stage,execution_options={'model_route':'ollama:fake'})
        from http.server import HTTPServer,BaseHTTPRequestHandler
        import threading
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['content-length'])));calls.append(payload)
                content=json.dumps({'purpose':'Record task evidence','scope_summary':'Record evidence for people'}) if 'purpose, scope_summary' in json.dumps(payload) else json.dumps({'title':'Record evidence','statement':'The system shall record task evidence for people.','priority':'must','acceptance':['Task evidence is recorded.']})
                if payload.get('stream'):
                    events=[{'id':'fake','model':'fake','choices':[{'index':0,'delta':{'role':'assistant','content':content},'finish_reason':None}]},
                        {'id':'fake','model':'fake','choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}]
                    body=b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n';ct='text/event-stream'
                else:
                    body=json.dumps({'id':'fake','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}).encode();ct='application/json'
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
        engine=ChildGeneratorEngine(tmp_path,gateway_factory=lambda cap,job:Gateway(job),dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
            command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(__import__('pathlib').Path(__file__).parents[2]/'interview-engine/src'),str(__import__('pathlib').Path(__file__).parents[2]/'spec-generator/src')])
        argv=[sys.executable,'-m','spec_generator.cli',str(ip),'--system-baseline',str(bp),'--out',str(out),'--to',stage]
        async def go():
            proc=VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_NEEDS':'ollama:fake'})
            assert await engine._worker(store,proc).execute(job,engine._produce(proc))
            assert await proc.wait()==0
            return await proc.stdout.read()
        try:
            if publishing:
                import delivery_api.artifact_publication as publication
                real=publication.publish_session_revision
                def fail(*a,**kw):raise RuntimeError('injected publication outage')
                monkeypatch.setattr(publication,'publish_session_revision',fail)
                with pytest.raises(RuntimeError,match='publication outage'):asyncio.run(go())
                assert store.producer_result(job) is not None
                monkeypatch.setattr(publication,'publish_session_revision',real)
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
            else:output=asyncio.run(go())
        finally:server.shutdown();server.server_close()
        assert (out/'needs.json').is_file() and b'Stopped after stage' in output
        assert store.worker_job(job)['state']=='done' and engine._quiescent
        if stage=='srs':assert calls and (out/'srs.json').is_file()


def test_manifest_rejects_foreign_or_changed_inputs(tmp_path):
    root=tmp_path/'owned';root.mkdir();outside=tmp_path/'foreign.json';outside.write_text('{}')
    with pytest.raises(PermissionError):manifest([str(outside),'--out',str(root/'spec')],root)
    owned=root/'intent.json';owned.write_text('{}')
    a,digest=manifest([str(owned),'--out',str(root/'spec')],root)
    owned.write_text('{"changed":true}')
    assert manifest([str(owned),'--out',str(root/'spec')],root)[1]!=digest


def test_durable_gate_answer_survives_parent_crash_without_reasking(store,tmp_path,monkeypatch):
    from delivery_api import config
    monkeypatch.setattr(config,'ARTIFACT_PUBLISH',False);monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    with tenant_context(A):
        tid=store.create_task('gate restart '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
        root=session_root(tmp_path,A,sid);root.mkdir(parents=True);ip=root/'intent.json';ip.write_text('{}')
        job=store.enqueue_worker(tid,'phase:needs',execution_options={'model_route':'ollama:fake'})
        class Gateway:
            retail_rates=None
            def __init__(self,job):self.job_id=job;self.store=store
        from pathlib import Path
        command=[sys.executable,str(Path(__file__).parent/'fixtures/child_generator_gate_fixture.py'),
            str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')]
        def engine():return ChildGeneratorEngine(tmp_path,gateway_factory=lambda cap,job:Gateway(job),dsn=DSN.replace('user=sandbox','user=delivery_app'),endpoint='http://127.0.0.1:1',command=command)
        argv=[sys.executable,'-m','spec_generator.cli',str(ip),'--out',str(root/'spec')];asked=[]
        async def go(e):
            proc=VirtualProcess(asyncio.get_running_loop());proc.attach(argv,{'SPEC_GENERATOR_MODEL_NEEDS':'ollama:fake'})
            def decide(d):asked.append(d);return 'a'
            proc.decide=decide
            assert await e._worker(store,proc).execute(job,e._produce(proc))
        original=store.retain_decision_review
        def crash_after_answer(task,wait,revision,payload):
            original(task,wait,revision,payload)
            if revision.startswith('answer:gate:'):raise RuntimeError('parent crashed after durable answer')
        monkeypatch.setattr(store,'retain_decision_review',crash_after_answer)
        with pytest.raises(RuntimeError,match='parent crashed'):asyncio.run(go(engine()))
        monkeypatch.setattr(store,'retain_decision_review',original);store.reauthorize_worker(job)
        asyncio.run(go(engine()))
        assert len(asked)==1 and json.loads((root/'spec/needs.json').read_text())['accepted'] is True
        assert store.worker_job(job)['state']=='done'


def test_items_phase_uses_generator_stage_not_delivery_specs_alias():
    from delivery_api.phases import PHASE_BY_KEY
    assert PHASE_BY_KEY['specs'].generator_stage=='items'
    # The typed request must never emit the delivery alias, which the strict
    # neutral contract intentionally rejects.
    import inspect
    assert "'phase':generator_stage" in inspect.getsource(ChildGeneratorEngine._produce)
