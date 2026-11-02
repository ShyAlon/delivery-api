"""Explicit attended-preview constructor. No default or deployed-cloud activation."""
from urllib.parse import urlsplit

FLAG='DELIVERY_PAID_CHILD_PREVIEW'


def require_preview(env):
    origin=urlsplit(env.get('DELIVERY_PUBLIC_ORIGIN',''))
    if (env.get(FLAG)!='1' or env.get('DELIVERY_PREVIEW')!='1'
        or env.get('DELIVERY_CONTROL_PLANE_ONLY','0')!='0'
        or origin.scheme!='http' or origin.hostname not in ('localhost','127.0.0.1')
        or origin.username is not None or origin.password is not None
        or origin.path not in ('','/') or origin.query or origin.fragment):
        raise ValueError('paid child requires explicit attended loopback preview')


def construct(env,gateways):
    if env.get('DELIVERY_CHILD_EXECUTION')!='attended-paid':return None
    require_preview(env)
    if set(gateways.models)=={'ollama'}:return None  # no paid authority is created for a local-only ready set
    from .child_paid_runtime import PaidChildRuntime
    runtime=PaidChildRuntime(models=gateways.models,api_key=env.get('DEEPSEEK_API_KEY',''))
    if hasattr(gateways,'for_task'):runtime.pin_factory=gateways
    return runtime
