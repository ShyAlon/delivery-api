"""Starts the in-process engines for cloud accounts, once, when the operator has opened execution.

Builds the money gateway from reviewed server configuration, then installs the generator-phase engine and the interview
host, and points the service's engine client at the hosted interview. Nothing runs until a user starts work. If any step
fails, nothing is installed, the reason is kept and execution stays closed (cloud_execution reports it)."""
from .diagnostic_evidence import thread_target
_state=None
_failure=None
_checked=set()
_watcher=None
VENDOR_WATCH_SECONDS=61*60   # just over the one-hour reuse of a good check, so each wake is a real check


def check_gateways(environ):
    """Raises AdmissionDenied when the reviewed prices, context ceiling or provider key do not build a gateway."""
    from . import production_gateway as pg
    key=tuple(environ.get(name) for name in (pg.PRICES_ENV,pg.CEILING_ENV,pg.KEY_ENV,'DELIVERY_LOCAL_PREVIEW'))
    if key in _checked:return      # the switch is asked on every request; a configuration that built once is not rebuilt
    pg.ProductionGateways(None,environ).close()
    _checked.add(key)


def failure():
    return _failure


def gateway_factory():
    """The installed callable gateway factory (factory(capability,job_id)), or None when execution is not installed."""
    return _state[0] if _state is not None else None


def installed():
    return _state is not None


def _clients(module):
    import httpx
    from .provider_network import deepseek_client_factory,ollama_client_factory
    inner_sync=lambda:httpx.HTTPTransport(retries=0)
    inner_async=lambda:httpx.AsyncHTTPTransport(retries=0)
    deepseek=deepseek_client_factory(inner_sync=inner_sync,inner_async=inner_async,owner_module=module)
    ollama=ollama_client_factory(inner_sync=inner_sync,inner_async=inner_async,owner_module=module)
    def factory(*,stage,provider,model,gateway):
        if provider=='deepseek':return deepseek(stage=stage,provider=provider,model=model,gateway=gateway)
        if provider=='ollama':return ollama(stage=stage,provider=provider,model=model,gateway=gateway)
        raise ValueError('reviewed provider client required')
    return factory


def _watch(gateways,stop,seconds):
    """Checks the vendor pages about hourly, so a failed refresh shows as a warning even when nobody is using the service."""
    from . import vendor_status
    while True:
        try:gateways.verify_vendor()
        except Exception as exc:vendor_status.warn('vendor-stop','the live check could not complete: '+f'{type(exc).__name__}: {str(exc)[:300]}')
        if stop.wait(seconds):return


def install(store,environ,set_engine_client,watch_vendor=False):
    global _state,_failure,_watcher
    if _state is not None:return
    from . import inprocess_engine,inprocess_interview
    from .production_gateway import ProductionGateways
    from vmodel_contracts.runtime import artifacts_root
    gateways=None
    try:
        gateways=ProductionGateways(store,environ)
        base=artifacts_root(environ.get('VMODEL_ARTIFACTS_DIR'))
        database_url=environ.get('DELIVERY_DATABASE_URL','')
        session_dsn=database_url if database_url.startswith(('postgres://','postgresql://')) else None
        from .child_pool_budget import child_mode
        if child_mode(environ):
            if set(gateways.models)!={'ollama'}:
                raise ValueError('local child mode requires only reviewed Ollama; paid child remains refused')
            if not session_dsn:raise ValueError('local child mode requires Postgres session persistence')
            from .child_generator import ChildGeneratorEngine
            from .child_interview import ChildInterviewHost
            endpoint=(environ.get('OLLAMA_BASE_URL') or 'http://localhost:11434/v1').rstrip('/')+'/chat/completions'
            engine=ChildGeneratorEngine(base,gateway_factory=gateways,dsn=session_dsn,endpoint=endpoint)
            host=ChildInterviewHost(store,base,gateway_factory=gateways,session_dsn=session_dsn,endpoint=endpoint)
        else:
            engine=inprocess_engine.InProcessEngine(base,gateway_factory=gateways,wire_client_factory=_clients('spec_generator'))
            host=inprocess_interview.InProcessInterviewHost(store,base,gateway_factory=gateways,
                generator_clients=_clients('spec_generator'),interview_clients=_clients('interview_engine'),session_dsn=session_dsn)
        client=host.client()
    except BaseException as exc:
        if gateways is not None:gateways.close()
        _failure=f'{type(exc).__name__}: {str(exc)[:300]}'
        raise
    _failure=None
    inprocess_engine.configure(engine);inprocess_interview.configure(host);set_engine_client(client)
    from . import engine_log
    engine_log.install_mirror()   # the in-process engine's stream reaches engine.log from here on
    _state=(gateways,engine,host)
    if watch_vendor and gateways.verify_vendor is not None:
        import threading
        stop=threading.Event()
        threading.Thread(target=thread_target(_watch),args=(gateways,stop,VENDOR_WATCH_SECONDS),name='vendor-terms-watch',daemon=True).start()
        _watcher=stop


def uninstall():
    global _state,_watcher
    if _state is None:return
    if _watcher is not None:_watcher.set();_watcher=None
    from . import inprocess_engine,inprocess_interview
    from . import engine_log
    engine_log.uninstall_mirror()
    gateways,host=_state[0],_state[2]
    inprocess_engine.configure(None);inprocess_interview.configure(None)
    _state=None
    # Release interview leases before the database gateways close: the release
    # writes go through the store connection.
    try:host.close()
    except Exception:pass
    gateways.close()
