"""Closed-by-default switch for running the product for cloud accounts.

Execution is admitted only when the operator sets DELIVERY_CLOUD_EXECUTION=1 in the server environment AND every startup
check below passes. Per-call protection is separate and always on: a reservation before every provider call, the prepaid
balance check, the per-owner admission row and the concurrency limit (attempt_ledger). A failed check keeps execution
closed; the reasons go to the server log, never to a user. Values of secrets are never logged."""
import os,sys

FLAG='DELIVERY_CLOUD_EXECUTION'
DEFAULT_ENGINE_TOKEN='local-interview-engine-token'


def problems(environ=None):
    """Reasons execution stays closed; an empty list means it is admitted."""
    from . import config
    from .execution_fence import control_plane_only
    environ=os.environ if environ is None else environ
    if environ.get(FLAG,'0')!='1':return [FLAG+' is not 1']
    out=[]
    if control_plane_only():out.append('the delivery-only runtime profile is active')
    if not config.CLOUD_TENANTS:out.append('cloud accounts (Supabase) are not configured')
    if not environ.get('DEEPSEEK_API_KEY','').strip():out.append('DEEPSEEK_API_KEY is not set')
    token=environ.get('INTERVIEW_ENGINE_TOKEN','')
    if not token or token==DEFAULT_ENGINE_TOKEN or len(token)<24:out.append('INTERVIEW_ENGINE_TOKEN is missing, default or shorter than 24 characters')
    try:
        from .admission_catalog import catalog_from_environment
        catalog_from_environment(environ)
    except Exception as exc:
        out.append('reviewed model prices: '+str(exc))
    try:
        from .cloud_runtime import check_gateways
        check_gateways(environ)
    except Exception as exc:
        out.append('money gateway: '+str(exc))
    from .cloud_runtime import failure
    if failure():out.append('in-process engine did not start: '+failure())
    return out


def admitted(environ=None):
    return not problems(environ)


def log_status(stream=None):
    stream=stream or sys.stderr
    reasons=problems()
    if not reasons:print('Cloud execution: OPEN (provider calls need an admission row and prepaid balance per owner)',file=stream)
    else:print('Cloud execution: CLOSED - '+'; '.join(reasons),file=stream)
