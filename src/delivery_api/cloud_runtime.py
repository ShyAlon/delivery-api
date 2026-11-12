"""Installs the one reviewed child runtime at every origin.

Builds the money gateway from reviewed server configuration, then installs the generator-phase engine and the interview
host, and points the service's engine client at the hosted interview. Nothing runs until a user starts work. If any step
fails, nothing is installed, the reason is kept and execution stays closed (cloud_execution reports it)."""
from .diagnostic_evidence import thread_target
import threading
_install_lock=threading.RLock()
_state=None
_failure=None
_failure_code=None
_checked=set()
_watcher=None
VENDOR_WATCH_SECONDS=61*60   # just over the one-hour reuse of a good check, so each wake is a real check


def check_gateways(environ):
    """The installed reviewed gateway is the same at every deployment origin."""
    if _state is None:raise ValueError('reviewed child runtime is not ready')
    if not _state[0].models:raise ValueError('no ready reviewed provider models')


def failure():
    return _failure


def gateway_factory():
    """The installed callable gateway factory (factory(capability,job_id)), or None when execution is not installed."""
    return _state[0] if _state is not None else None


def unavailable_reason():
    return _failure_code if _state is None and _failure_code=='no_approved_model_pin' else 'runtime_unavailable'


def installed():
    return _state is not None


def _watch(gateways,stop,seconds):
    """Checks the vendor pages about hourly, so a failed refresh shows as a warning even when nobody is using the service."""
    from . import vendor_status
    while True:
        try:gateways.verify_vendor()
        except Exception as exc:vendor_status.warn('vendor-stop','the live check could not complete: '+f'{type(exc).__name__}: {str(exc)[:300]}')
        if stop.wait(seconds):return


def install(store,environ,set_engine_client,watch_vendor=False,*,paid_runtime=None):
    with _install_lock:
        return _install(store,environ,set_engine_client,watch_vendor,paid_runtime=paid_runtime)


def _install(store,environ,set_engine_client,watch_vendor=False,*,paid_runtime=None):
    global _state,_failure,_failure_code,_watcher
    if _state is not None:return
    from . import inprocess_engine
    from vmodel_contracts.runtime import artifacts_root
    gateways=None
    try:
        from .model_pins import DurableGateways
        gateways=DurableGateways(store,environ)
        gateways.refresh_launch()
        if paid_runtime is None:
            from .provider_runtime import construct
            paid_runtime=construct(environ,gateways)
        base=artifacts_root(environ.get('VMODEL_ARTIFACTS_DIR'))
        session_dsn=environ.get('DELIVERY_DATABASE_URL','')
        if not session_dsn:raise ValueError('Postgres session persistence is required')
        if paid_runtime is not None:
            from .child_paid_runtime import PaidChildRuntime
            if not isinstance(paid_runtime,PaidChildRuntime):raise TypeError('typed parent accounting runtime required')
            if gateways.models!=paid_runtime.models:raise ValueError('child models differ from reviewed gateways')
        from .child_generator import ChildGeneratorEngine
        from .child_interview import ChildInterviewHost
        from .model_registry import ollama_base
        endpoint=ollama_base(environ)+'/chat/completions'
        engine=ChildGeneratorEngine(base,gateway_factory=gateways,dsn=session_dsn,endpoint=endpoint,paid_runtime=paid_runtime)
        from .answer_mode import from_environment
        answer_mode=from_environment(environ,paid_runtime)
        if answer_mode is not None:
            from .answer_mode import check_installed_schema
            check_installed_schema(session_dsn)
        host=ChildInterviewHost(store,base,gateway_factory=gateways,session_dsn=session_dsn,endpoint=endpoint,paid_runtime=paid_runtime,answer_accounting=answer_mode)
        client=host.client()
    except BaseException as exc:
        if gateways is not None:gateways.close()
        _failure_code='no_approved_model_pin' if getattr(exc,'code',None)=='no_approved_model_pin' else 'runtime_unavailable'
        _failure=f'{type(exc).__name__}: {str(exc)[:300]}'
        raise
    _failure=None;_failure_code=None
    inprocess_engine.configure(engine);set_engine_client(client)
    from . import engine_log
    engine_log.install_mirror()
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
    from . import inprocess_engine
    from . import engine_log
    engine_log.uninstall_mirror()
    gateways,host=_state[0],_state[2]
    inprocess_engine.configure(None)
    _state=None
    # Release interview leases before the database gateways close: the release
    # writes go through the store connection.
    try:host.close()
    except Exception:pass
    gateways.close()
