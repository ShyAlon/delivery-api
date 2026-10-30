"""Run the interview engine inside the service, every model call under the money gateway.

The service already talks to the interview through one client (engine_client). This module hosts the engine's own
application in this process and gives that client a transport straight into it, so every route, error and snapshot
stays the engine's own. What changes is where model calls happen: each interview step (build the program, ask the
first question, take an answer) is one durable worker job, claimed and metered by the same-host producer worker, so
the interview is admitted and measured exactly like a generator phase.

Limits, by design of running in one process:
  * A step blocks the engine's request thread while it runs (as it did over HTTP).
  * The worker is the only writer of the session's stop marker (one writer lock per session); the engine registry's
    own writer-lock use is replaced, its short marker checks stay.
  * Stop is cooperative; a pending answer survives a restart through the engine's session file.
"""
from .diagnostic_evidence import thread_target
import asyncio,secrets,threading
from contextlib import contextmanager
from pathlib import Path
import httpx

_BASE_URL='http://interview-engine.internal'
_TIMEOUT_S=3600.0


class _HostedEngineTransport(httpx.BaseTransport):
    """Synchronous httpx transport into an ASGI app that runs on its own event loop thread.

    The caller's tenant scope is carried into the app: the engine's threads see it as the current scope."""
    def __init__(self,app):
        self._asgi=httpx.ASGITransport(app=app)
        self._loop=asyncio.new_event_loop()
        self._thread=threading.Thread(target=thread_target(self._loop.run_forever),name='interview-engine-host',daemon=True)
        self._thread.start()

    def handle_request(self,request):
        from .tenancy import current_scope,tenant_context
        scope=current_scope()
        async def call():
            with tenant_context(scope):
                response=await self._asgi.handle_async_request(request)
                body=await response.aread()
                headers=[(k,v) for k,v in response.headers.items() if k.lower() not in ('content-length','transfer-encoding','content-encoding')]
                return httpx.Response(response.status_code,headers=headers,content=body,request=request)
        return asyncio.run_coroutine_threadsafe(call(),self._loop).result(_TIMEOUT_S)

    def close(self):
        self._loop.call_soon_threadsafe(self._loop.stop)


class InProcessInterviewHost:
    def __init__(self,store,artifacts_base,*,gateway_factory,generator_clients,interview_clients,session_dsn=None):
        self.store=store
        self.artifacts_base=Path(artifacts_base)
        self.gateway_factory=gateway_factory
        self.generator_clients=generator_clients
        self.interview_clients=interview_clients
        self._session_dsn=session_dsn
        self._token=secrets.token_hex(24)
        self._client=None
        self._lock=threading.Lock()
        from contextvars import ContextVar
        self._route_target=ContextVar('interview_job_route_target',default=None)

    def client(self):
        """The engine client the service uses; created once."""
        with self._lock:
            if self._client is None:
                from .engine_client import HttpEngineClient
                self._client=HttpEngineClient(_BASE_URL,token=self._token,transport=_HostedEngineTransport(self._app()))
            return self._client

    def _app(self):
        from interview_engine.http_service import create_app,default_program_factory
        from interview_engine.tenant_worker import TenantRegistryRouter,TenantWorkerMiddleware
        registry_type=self._registry_type()
        # One pool for the whole process: delivery's shared pool (db_pool) is
        # injected into the engine's router, so the engine never builds a
        # second pool and the connection budget stays one number.
        engine_pool=None
        if self._session_dsn is not None:
            from .db_pool import pool_for
            engine_pool=pool_for(self._session_dsn)
        # The engine receives the same required PostgreSQL session persistence.
        installed_resolver=None
        if self._session_dsn is not None:
            from .pg_execution_fence import InstalledInterviewControlRouter
            installed_resolver=InstalledInterviewControlRouter(self.store,self.artifacts_base).resolve
        self._engine_router=TenantRegistryRouter(registry_type,default_program_factory,self.artifacts_base,session_dsn=self._session_dsn,allow_file_sessions=self._session_dsn is None,pool=engine_pool,installed_resolver=installed_resolver)
        quality_store=None
        if self._session_dsn is not None:
            from .pg_quality_config import PostgresQualityConfig
            quality_store=PostgresQualityConfig(self._session_dsn)
        self._quality_store=quality_store
        app=create_app(self._engine_router,service_token=self._token,quality_store=quality_store)
        app.add_middleware(TenantWorkerMiddleware,base=self.artifacts_base,service_token=self._token)
        return app

    def close(self):
        # Shutdown: closing the router closes each per-owner engine registry,
        # which releases held leases at committed boundaries (no TTL wait).
        router=getattr(self,'_engine_router',None)
        if router is not None:
            try:router.close()
            except Exception:pass

    def _registry_type(self):
        from interview_engine.http_service import SessionRegistry
        host=self
        class GatedRegistry(SessionRegistry):
            """The engine's registry with each model-calling step run as a gated job."""
            def switch_route(self,session_id,request):
                # Candidate construction precedes installation on session.program.
                # Bind the explicit target, not the old live program's route.
                from interview_engine import session_models
                target=session_models.resolve(request.model_route,request.model)
                token=host._route_target.set(target)
                try:return super().switch_route(session_id,request)
                finally:host._route_target.reset(token)
            def _prepare_program(self,session,build):
                return host.run_step(session,build)
            def _advance(self,session,response=None):
                def advance():
                    return session.program.advance() if response is None else session.program.advance(response)
                result=host.run_step(session,advance)
                if session.stop_fence is not None:session.stop_fence.check(session.stop_fence.generation)
                return result
        return GatedRegistry

    def _worker(self):
        from .same_host_producer import SameHostProducerWorker
        from spec_generator.telemetry import UsageLog
        return SameHostProducerWorker(self.store,self.artifacts_base,gateway_factory=self.gateway_factory,
            usage_log_factory=lambda capability,root:UsageLog(capability.session_id),
            wire_client_factory=self.generator_clients,documents_enabled=hasattr(self.store,'_postgres'))

    @contextmanager
    def _bound(self,capability,job_id):
        """The interview library's own bindings for one job: gateway, wire clients, and the worker's writer lease."""
        from contextlib import ExitStack
        from interview_engine.attempt_admission import admission_gateway
        from interview_engine.wire_binding import wire_clients
        from interview_engine import stop_fence as interview_fence
        from spec_generator import stop_fence as generator_fence
        from spec_generator.cooperative_run import cli_stop_binding
        fence,generation=cli_stop_binding()
        gateway=self.gateway_factory(capability,job_id)
        tokens=[(interview_fence._check,interview_fence._check.set(lambda:fence.check(generation))),
            (interview_fence._current,interview_fence._current.set(fence)),
            (interview_fence._publish,interview_fence._publish.set(generator_fence.guarded_publish))]
        try:
            with ExitStack() as stack:
                quality_store=getattr(self,'_quality_store',None)
                if quality_store is not None:
                    from interview_engine.question_config import bound_store
                    stack.enter_context(bound_store(quality_store))
                stack.enter_context(admission_gateway(gateway,required=True))
                stack.enter_context(wire_clients(gateway,self.interview_clients))
                yield
        finally:
            for variable,token in reversed(tokens):variable.reset(token)

    def _execution_options(self,session):
        """Freeze the selected route before metering, never guess from usage.

        Single-provider legacy jobs keep their existing contract. With two
        admitted providers there must be exactly one trustworthy model map.
        """
        if len(getattr(self.gateway_factory,'models',{}))<2:return {}
        from .attempt_ledger import AdmissionDenied
        models=self._route_target.get() or getattr(getattr(session,'program',None),'model_map',None)
        if not models:
            request=getattr(session,'request',None)
            if request is not None:
                from interview_engine import session_models
                models=session_models.resolve(request.model_route,request.initial_model)
        try:pairs={tuple(pair) for pair in (models or {}).values()}
        except (TypeError,ValueError):raise AdmissionDenied('one pinned interview provider:model required') from None
        if len(pairs)!=1:raise AdmissionDenied('one pinned interview provider:model required')
        pair=next(iter(pairs))
        if len(pair)!=2 or self.gateway_factory.models.get(pair[0])!=pair[1]:
            raise AdmissionDenied('pinned interview provider:model is not reviewed')
        return {'model_route':pair[0]+':'+pair[1]}

    def run_step(self,session,call):
        """Run one model-calling step as a claimed worker job; return what it returned."""
        from .tenancy import current_scope,tenant_context
        from interview_engine.stop_fence import StopRequested
        scope=current_scope()
        box={}
        async def go():
            with tenant_context(scope):
                job_id=self.store.enqueue_worker(session.task_id,'phase:intent',execution_options=self._execution_options(session))
                def producer(capability,root):
                    from .diagnostic_evidence import bound,failure
                    with bound(task_id=session.task_id,session_id=getattr(session,'session_id',None),job_id=job_id):
                        try:
                            with self._bound(capability,job_id):box['result']=call()
                        except StopRequested:raise
                        except Exception:
                            failure('interview.worker.failed')
                            raise
                if not await self._worker().execute(job_id,producer):
                    raise RuntimeError('interview job was already claimed; no step ran')
        asyncio.run(go())
        if 'result' not in box:
            # An expected Stop ends the job quietly; the engine treats it as a stopped session.
            raise StopRequested('session stop requested')
        return box['result']


_host=None

def configure(host):
    global _host
    _host=host

def current():
    return _host
