import asyncio,sys,threading,types
from pathlib import Path
import httpx
import pytest
from delivery_api import inprocess_interview as ii
from delivery_api.tenancy import TenantScope,tenant_context,current_scope

OWNER='11111111-1111-4111-8111-111111111111'


def scope():return TenantScope(OWNER,'22222222-2222-4222-8222-222222222222')


async def echo_app(asgi_scope,receive,send):
    """A tiny ASGI app: reports the tenant scope its coroutine and a worker thread see, and echoes the body."""
    body=b''
    while True:
        message=await receive()
        body+=message.get('body',b'')
        if not message.get('more_body'):break
    seen=current_scope().owner_id
    in_thread={}
    t=threading.Thread(target=lambda:None);t.start();t.join()
    status=404 if asgi_scope['path']=='/missing' else 200
    payload=('%s|%s|%s'%(seen,asgi_scope['method'],body.decode())).encode()
    await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'text/plain'),(b'x-seen',seen.encode())]})
    await send({'type':'http.response.body','body':payload})


def test_transport_reaches_the_hosted_app_and_carries_the_tenant_scope():
    transport=ii._HostedEngineTransport(echo_app)
    client=httpx.Client(base_url='http://x',transport=transport)
    try:
        with tenant_context(scope()):
            r=client.post('/v1/sessions',content=b'hello')
            assert r.status_code==200 and r.text==OWNER+'|POST|hello' and r.headers['x-seen']==OWNER
            assert client.get('/missing').status_code==404
    finally:
        client.close();transport.close()


def test_transport_without_a_scope_is_refused():
    transport=ii._HostedEngineTransport(echo_app)
    client=httpx.Client(base_url='http://x',transport=transport)
    try:
        with pytest.raises(Exception):client.get('/v1/sessions/abc')
    finally:
        client.close();transport.close()


class Store:
    def __init__(self):self.jobs=[]
    def enqueue_worker(self,task_id,kind,*,execution_options=None):
        self.jobs.append((task_id,kind,execution_options));return 'job%d'%len(self.jobs)


class Session:
    task_id='task-1';stop_fence=None


def host_with(claimed=True,runs=None,quiet_stop=False):
    host=ii.InProcessInterviewHost(Store(),Path('/tmp'),gateway_factory=lambda c,j:None,generator_clients=None,interview_clients=None)
    log=[] if runs is None else runs
    class Worker:
        async def execute(self,job_id,producer):
            if not claimed:return False
            log.append(('claimed',job_id,current_scope().owner_id))
            if not quiet_stop:producer(types.SimpleNamespace(session_id='s'),Path('/tmp'))
            return True
    host._worker=lambda:Worker()
    import contextlib
    @contextlib.contextmanager
    def bound(capability,job_id):
        log.append(('bound',job_id));yield;log.append(('unbound',job_id))
    host._bound=bound
    return host,log


@pytest.fixture
def fake_engine(monkeypatch):
    mod=types.ModuleType('interview_engine');mod.__path__=[]
    sf=types.ModuleType('interview_engine.stop_fence')
    class StopRequested(RuntimeError):pass
    sf.StopRequested=StopRequested
    monkeypatch.setitem(sys.modules,'interview_engine',mod);monkeypatch.setitem(sys.modules,'interview_engine.stop_fence',sf)
    return StopRequested


def test_a_step_is_one_claimed_job_and_returns_the_steps_result(fake_engine):
    host,log=host_with()
    with tenant_context(scope()):
        assert host.run_step(Session(),lambda:'question 1')=='question 1'
        assert host.run_step(Session(),lambda:None) is None      # a finished interview legitimately returns nothing
    assert host.store.jobs==[('task-1','phase:intent',{}),('task-1','phase:intent',{})]
    assert log[:4]==[('claimed','job1',OWNER),('bound','job1'),('unbound','job1'),('claimed','job2',OWNER)]


def test_an_unclaimed_job_runs_no_step(fake_engine):
    host,log=host_with(claimed=False);ran=[]
    with tenant_context(scope()):
        with pytest.raises(RuntimeError,match='already claimed'):host.run_step(Session(),lambda:ran.append(1))
    assert ran==[]


def test_a_quiet_stop_is_reported_as_stop_requested(fake_engine):
    host,log=host_with(quiet_stop=True)
    with tenant_context(scope()):
        with pytest.raises(fake_engine):host.run_step(Session(),lambda:'never')


def test_a_failing_step_propagates(fake_engine):
    host,log=host_with()
    def boom():raise ValueError('model said no')
    with tenant_context(scope()):
        with pytest.raises(ValueError,match='model said no'):host.run_step(Session(),boom)


def test_gated_registry_sends_both_model_steps_through_the_job(fake_engine,monkeypatch):
    http=types.ModuleType('interview_engine.http_service')
    class SessionRegistry:
        def __init__(self,*a,**k):pass
        def _prepare_program(self,session,build):return build()
        def _advance(self,session,response=None):raise AssertionError('the base advance must not run')
    http.SessionRegistry=SessionRegistry
    monkeypatch.setitem(sys.modules,'interview_engine.http_service',http)
    host,log=host_with()
    registry=host._registry_type()()
    program=types.SimpleNamespace(advance=lambda *a:('asked',a))
    session=Session();session.program=program
    with tenant_context(scope()):
        assert registry._prepare_program(session,lambda:'built')=='built'
        assert registry._advance(session)==('asked',())
        assert registry._advance(session,'answer')==('asked',('answer',))
    assert len(host.store.jobs)==3


def test_gated_registry_checks_the_stop_marker_after_a_step(fake_engine,monkeypatch):
    http=types.ModuleType('interview_engine.http_service')
    class SessionRegistry:
        def __init__(self,*a,**k):pass
    http.SessionRegistry=SessionRegistry
    monkeypatch.setitem(sys.modules,'interview_engine.http_service',http)
    host,log=host_with()
    checked=[]
    session=Session();session.program=types.SimpleNamespace(advance=lambda *a:'x')
    session.stop_fence=types.SimpleNamespace(generation=3,check=lambda g:checked.append(g))
    with tenant_context(scope()):
        host._registry_type()()._advance(session)
    assert checked==[3]


def test_the_client_is_built_once_and_uses_the_hosted_transport(fake_engine,monkeypatch):
    host,log=host_with()
    monkeypatch.setattr(host,'_app',lambda:echo_app)
    ec=types.ModuleType('delivery_api.engine_client')
    class HttpEngineClient:
        def __init__(self,base_url,*,token,transport):self._http=httpx.Client(base_url=base_url,transport=transport)
    ec.HttpEngineClient=HttpEngineClient
    monkeypatch.setitem(sys.modules,'delivery_api.engine_client',ec)
    a=host.client();b=host.client()
    try:
        assert a is b
        with tenant_context(scope()):
            assert a._http.get('/anything').text.startswith(OWNER)
    finally:a._http.close()


def test_configure_and_current():
    saved=ii._host
    try:
        ii.configure(None);assert ii.current() is None
        ii.configure('h');assert ii.current()=='h'
    finally:ii.configure(saved)

def test_dual_interview_step_pins_program_route_in_durable_job_options(fake_engine):
    host,log=host_with()
    host.gateway_factory.models={'deepseek':'deepseek-flash','ollama':'phi4'}
    session=Session();session.program=types.SimpleNamespace(model_map={'probing':('ollama','phi4'),'conops':('ollama','phi4')})
    with tenant_context(scope()):
        assert host.run_step(session,lambda:'local result')=='local result'
    assert host.store.jobs==[('task-1','phase:intent',{'model_route':'ollama:phi4'})]


def test_dual_interview_missing_or_divergent_route_refuses_before_enqueue(fake_engine):
    from delivery_api.attempt_ledger import AdmissionDenied
    host,log=host_with();host.gateway_factory.models={'deepseek':'deepseek-flash','ollama':'phi4'}
    with tenant_context(scope()):
        with pytest.raises(AdmissionDenied,match='pinned'):host.run_step(Session(),lambda:None)
        session=Session();session.program=types.SimpleNamespace(model_map={'probing':('ollama','phi4'),'conops':('deepseek','deepseek-flash')})
        with pytest.raises(AdmissionDenied,match='pinned'):host.run_step(session,lambda:None)
    assert host.store.jobs==[]


def test_dual_interview_initial_request_and_switch_target_are_pinned_before_metering(fake_engine,monkeypatch):
    host,log=host_with();host.gateway_factory.models={'deepseek':'deepseek-flash','ollama':'phi4'}
    models=types.ModuleType('interview_engine.session_models')
    models.resolve=lambda route,model:{'probing':(route,model or 'phi4')}
    monkeypatch.setitem(sys.modules,'interview_engine.session_models',models)
    session=Session();session.request=types.SimpleNamespace(model_route='ollama',initial_model='phi4')
    with tenant_context(scope()):assert host.run_step(session,lambda:'constructed')=='constructed'
    assert host.store.jobs[-1][2]=={'model_route':'ollama:phi4'}
    # Old session program must not select the accounting of a new candidate.
    session.program=types.SimpleNamespace(model_map={'probing':('ollama','phi4')})
    http=types.ModuleType('interview_engine.http_service')
    class Registry:
        def switch_route(self,session_id,request):
            assert host._execution_options(session)=={'model_route':'deepseek:deepseek-flash'}
            raise RuntimeError('candidate refuses')
    http.SessionRegistry=Registry
    monkeypatch.setitem(sys.modules,'interview_engine.http_service',http)
    registry=host._registry_type()()
    with pytest.raises(RuntimeError,match='candidate refuses'):
        registry.switch_route('s',types.SimpleNamespace(model_route='deepseek',model='deepseek-flash'))
    assert host._execution_options(session)=={'model_route':'ollama:phi4'}
