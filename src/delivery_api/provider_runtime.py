"""Parent-held provider transport and accounting, independent of deployment origin.

Construction makes no call and grants no spending authority. Every child dispatch
still requires its durable job capability, reviewed quote and prepaid admission.
"""
def construct(env,gateways):
    models=gateways.models
    if not models:raise ValueError('reviewed provider models required')
    if set(models)=={'ollama'}:return None
    from .child_paid_runtime import PaidChildRuntime
    runtime=PaidChildRuntime(models=models,api_key=env.get('DEEPSEEK_API_KEY',''))
    runtime.pin_factory=gateways
    return runtime
