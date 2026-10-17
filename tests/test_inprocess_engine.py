import asyncio,os,signal,sys,threading,types
from pathlib import Path
import pytest
from delivery_api import inprocess_engine as ie


def run(coro):return asyncio.run(coro)


async def read_all(proc):
    out=b''
    while True:
        chunk=await proc.stdout.read(512)
        if not chunk:return out
        out+=chunk


def test_lines_and_exit_code_reach_the_reader():
    async def go():
        proc=ie.VirtualProcess(asyncio.get_running_loop())
        sink=ie._Sink(proc)
        def producer():
            sink.write('one\ntw');sink.write('o\npartial');sink.flush();proc.finish(0)
        threading.Thread(target=producer).start()
        data=await read_all(proc)
        assert data==b'one\ntwo\npartial'
        assert await proc.wait()==0 and proc.returncode==0
    run(go())


def test_typed_decision_round_trip():
    async def go():
        proc=ie.VirtualProcess(asyncio.get_running_loop())
        got={}
        def producer():
            got['answer']=proc.decide({'decision_id':'d1','prompt':'Approve?'})
            proc.finish(0)
        t=threading.Thread(target=producer);t.start()
        line=await proc.stdout.readline()
        assert line.startswith(b'REQUIRE_EVENT {') and b'"decision_id":"d1"' in line
        proc.stdin.write(b'y\n');await proc.stdin.drain()
        await proc.wait();t.join()
        assert got['answer']=='y'
    run(go())


def test_kill_releases_the_runner_and_unblocks_a_waiting_question():
    async def go():
        proc=ie.VirtualProcess(asyncio.get_running_loop())
        seen={}
        def producer():
            try:proc.decide({'decision_id':'d','prompt':'?'})
            except ie.EngineStopped:seen['stopped']=True
        t=threading.Thread(target=producer);t.start()
        await proc.stdout.readline()
        proc.terminate()
        t.join(5);assert seen=={'stopped':True}
        assert proc.returncode is None          # terminate asks; only kill releases
        proc.kill()
        assert await asyncio.wait_for(proc.wait(),2)==-9
        assert await proc.stdout.read(10)==b''
    run(go())


def test_virtual_process_is_never_signalled(monkeypatch):
    calls=[]
    monkeypatch.setattr(os,'killpg',lambda *a:calls.append(a))
    async def go():
        proc=ie.VirtualProcess(asyncio.get_running_loop())
        assert proc.pid is None and proc.virtual
        ie.terminate(proc,signal.SIGTERM);ie.terminate(proc,signal.SIGKILL)
        assert calls==[]
    run(go())
    class Child:
        pid=4242;virtual=False
        def terminate(self):calls.append('terminate')
        def kill(self):calls.append('kill')
    ie.terminate(Child(),signal.SIGTERM)
    assert calls==[(4242,signal.SIGTERM)]


def test_process_settings_are_restored_exactly(monkeypatch):
    monkeypatch.setenv('VMODEL_KEEP','before');monkeypatch.setenv('SPEC_GENERATOR_OLD','old');monkeypatch.setenv('OTHER','x')
    monkeypatch.delenv('VMODEL_ADDED',raising=False);monkeypatch.delenv('SPEC_GENERATOR_FROM',raising=False)
    with ie.process_settings({'VMODEL_KEEP':'during','VMODEL_ADDED':'1','PYTHONUNBUFFERED':'1','OTHER':'changed'}):
        assert os.environ['VMODEL_KEEP']=='during' and os.environ['VMODEL_ADDED']=='1'
        assert os.environ['OTHER']=='x' and 'PYTHONUNBUFFERED' not in os.environ
        os.environ['SPEC_GENERATOR_FROM']='needs';os.environ['SPEC_GENERATOR_OLD']='generator-changed'
        os.environ.setdefault('VMODEL_LLM_LOG','/tmp/x')
    assert os.environ['VMODEL_KEEP']=='before' and os.environ['SPEC_GENERATOR_OLD']=='old'
    for k in ('VMODEL_ADDED','SPEC_GENERATOR_FROM','VMODEL_LLM_LOG'):assert k not in os.environ


def fake_spec_generator(monkeypatch,main):
    pkg=types.ModuleType('spec_generator');pkg.__path__=[]
    cli=types.ModuleType('spec_generator.cli');cli.main=main
    cli_io=types.ModuleType('spec_generator.cli_io')
    class RunIO:
        def __init__(self,stdout,stderr,decide):self.stdout,self.stderr,self.decide=stdout,stderr,decide
    cli_io.RunIO=RunIO
    errors=types.ModuleType('spec_generator.errors')
    class UserFacingError(Exception):
        def render(self):return 'friendly: '+str(self)
    errors.UserFacingError=UserFacingError
    mc=types.ModuleType('spec_generator.model_config')
    class StageModel:
        def __init__(self,provider,model,max_tokens=8192):self.provider,self.model,self.max_tokens=provider,model,max_tokens
    mc.StageModel=StageModel
    mc._STAGE_DEFAULTS={'needs':StageModel('ollama','x'),'tests':StageModel('ollama','x',max_tokens=12288)}
    for name,mod in (('spec_generator',pkg),('spec_generator.cli',cli),('spec_generator.cli_io',cli_io),('spec_generator.errors',errors),('spec_generator.model_config',mc)):
        monkeypatch.setitem(sys.modules,name,mod)
    return errors


def test_stage_models_only_the_one_deepseek_route(monkeypatch):
    fake_spec_generator(monkeypatch,lambda *a,**k:None)
    models=ie.stage_models({'SPEC_GENERATOR_MODEL_SRS':'deepseek:deepseek-v4','PATH':'/bin'})
    assert set(models)==set(ie._STAGES) and {(m.provider,m.model) for m in models.values()}=={('deepseek','deepseek-v4')}
    assert models['tests'].max_tokens==12288 and models['needs'].max_tokens==8192 and models['repair'].max_tokens==8192
    for env in ({},{'SPEC_GENERATOR_MODEL_SRS':'ollama:phi4'},{'SPEC_GENERATOR_MODEL_SRS':'deepseek:'},
                {'SPEC_GENERATOR_MODEL_SRS':'deepseek:a','SPEC_GENERATOR_MODEL_NEEDS':'deepseek:b'}):
        with pytest.raises(ValueError):ie.stage_models(env)


class Harness:
    """Stand-ins for the worker (claim + thread) and the phase orchestrator (reads the virtual process)."""
    def __init__(self,claimed=True):
        self.claimed=claimed;self.order=[];self.read=[];self.codes=[]
    def worker(self,engine):
        harness=self
        class Worker:
            async def execute(self_,job_id,producer):
                if not harness.claimed:return False
                proc=ie.take(harness.task_id)
                proc._loop.call_soon_threadsafe(proc.started.set)
                harness.order.append('claimed '+job_id)
                await asyncio.to_thread(lambda:(proc.wait_command(),producer(None,None)))
                return True
        engine._worker=lambda store,proc:Worker()
    class Orchestrator:
        def __init__(self,harness):self.h=harness
        async def execute_phase(self,task_id,phase,**options):
            proc=ie.take(task_id)
            proc.attach(['python','-m','spec_generator.cli','intent.json','--out','/o'],{'SPEC_GENERATOR_MODEL_SRS':'deepseek:m','PATH':'/bin'})
            self.h.order.append('phase '+phase)
            self.h.read.append((await read_all(proc)).decode())
            self.h.codes.append(await proc.wait())


def make(monkeypatch,main,claimed=True):
    errors=fake_spec_generator(monkeypatch,main)
    engine=ie.InProcessEngine(Path('/tmp'),gateway_factory=None,wire_client_factory=None)
    h=Harness(claimed);h.task_id='t1';h.worker(engine)
    return engine,h,errors


def test_dispatch_runs_generator_and_phase_lifecycle_together(monkeypatch):
    seen={}
    def main(argv,io):
        seen['argv']=argv;seen['env']=os.environ.get('SPEC_GENERATOR_MODEL_SRS')
        io.stdout.write('VMODEL_PROGRESS_JSONL:{}\n');io.stderr.write('note\n')
    engine,h,_=make(monkeypatch,main)
    before=dict(os.environ)      # whatever the machine already has (a parent .env may set this name) must survive
    assert run(engine.dispatch(None,Harness.Orchestrator(h),'t1','srs','job1',{}))is True
    assert seen['argv']==['intent.json','--out','/o'] and seen['env']=='deepseek:m'
    assert dict(os.environ)==before
    assert h.read==['VMODEL_PROGRESS_JSONL:{}\nnote\n'] and h.codes==[0]
    assert h.order==['claimed job1','phase srs'] and ie.take('t1') is None


def test_unclaimed_job_starts_no_phase(monkeypatch):
    engine,h,_=make(monkeypatch,lambda *a,**k:None,claimed=False)
    assert run(engine.dispatch(None,Harness.Orchestrator(h),'t1','srs','job1',{})) is False
    assert h.order==[] and h.codes==[] and ie.take('t1') is None


@pytest.mark.parametrize('raised,code,text',[('user',1,'friendly: bad input'),('exit3',3,''),('exit',0,''),('stopped',130,'')])
def test_exit_codes_reach_the_runner(monkeypatch,raised,code,text):
    box={}
    def main(argv,io):
        e=box['errors']
        if raised=='user':raise e.UserFacingError('bad input')
        if raised=='exit3':raise SystemExit(3)
        if raised=='exit':raise SystemExit(None)
        raise ie.EngineStopped('x')
    engine,h,errors=make(monkeypatch,main);box['errors']=errors
    run(engine.dispatch(None,Harness.Orchestrator(h),'t1','srs','j',{}))
    assert h.codes==[code] and text in h.read[0]


def test_unexpected_error_is_reported_to_the_runner_and_raised(monkeypatch):
    def main(argv,io):raise RuntimeError('boom')
    engine,h,_=make(monkeypatch,main)
    with pytest.raises(RuntimeError,match='boom'):
        run(engine.dispatch(None,Harness.Orchestrator(h),'t1','srs','j',{}))
    assert h.codes==[1] and 'RuntimeError: boom' in h.read[0] and ie.take('t1') is None


def test_one_phase_at_a_time(monkeypatch):
    active=[];peak=[]
    def main(argv,io):
        import time
        active.append(1);peak.append(len(active));time.sleep(0.05);active.pop()
    engine,h,_=make(monkeypatch,main)
    async def go():
        class Orch(Harness.Orchestrator):
            async def execute_phase(self,task_id,phase,**o):
                await super().execute_phase(task_id,phase,**o)
        h.task_id='t1'
        await asyncio.gather(engine.dispatch(None,Orch(h),'t1','srs','a',{}),engine.dispatch(None,Orch(h),'t1','needs','b',{}))
    run(go())
    assert max(peak)==1 and h.codes==[0,0]


def test_the_decision_handler_is_what_the_generator_sees(monkeypatch):
    answers=[]
    def main(argv,io):answers.append(io.decide({'decision_id':'d9','prompt':'Approve SRS?'}))
    engine,h,_=make(monkeypatch,main)
    class Orch(Harness.Orchestrator):
        async def execute_phase(self,task_id,phase,**o):
            proc=ie.take(task_id)
            proc.attach(['x','spec_generator.cli','i'],{'SPEC_GENERATOR_MODEL_SRS':'deepseek:m'})
            line=await proc.stdout.readline();h.read.append(line.decode())
            proc.stdin.write(b'approve\n')
            await read_all(proc);h.codes.append(await proc.wait())
    run(engine.dispatch(None,Orch(h),'t1','srs','j',{}))
    assert answers==['approve'] and h.read[0].startswith('REQUIRE_EVENT ') and h.codes==[0]


def test_runner_and_dispatch_wiring_in_source():
    root=Path(ie.__file__).parent
    runner=(root/'runner.py').read_text();jobs=(root/'tenant_jobs.py').read_text()
    assert runner.count('os.killpg(')==0 and runner.count('inprocess_engine.terminate(')==6
    assert 'virtual if virtual is not None else await asyncio.create_subprocess_exec(' in runner
    assert 'if config.CLOUD_TENANTS and virtual is None:' in runner
    assert jobs.count('engine.dispatch(')==1 and 'inprocess_engine.current()' in jobs


def test_without_a_configured_engine_nothing_is_taken():
    assert ie.take('nobody') is None
    saved=ie._engine
    try:
        ie.configure(None);assert ie.current() is None
    finally:ie.configure(saved)


@pytest.mark.parametrize('preset',[None,'ollama:phi4'])
def test_phase_settings_do_not_leak_whether_or_not_the_name_was_already_set(monkeypatch,preset):
    if preset is None:monkeypatch.delenv('SPEC_GENERATOR_MODEL_SRS',raising=False)
    else:monkeypatch.setenv('SPEC_GENERATOR_MODEL_SRS',preset)
    seen={}
    def main(argv,io):seen['during']=os.environ.get('SPEC_GENERATOR_MODEL_SRS')
    engine,h,_=make(monkeypatch,main)
    run(engine.dispatch(None,Harness.Orchestrator(h),'t1','srs','job1',{}))
    assert seen['during']=='deepseek:m' and os.environ.get('SPEC_GENERATOR_MODEL_SRS')==preset
