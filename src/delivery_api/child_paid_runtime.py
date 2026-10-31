"""Trusted constructor seam for paid-child acceptance, not production activation.

Only the explicit attended loopback preview constructs this object. Keys/transport stay in the parent;
child configuration receives only its pinned model and private descriptors.
"""
from .child_compute_channel import ChildComputeAccounting


class PaidChildRuntime:
    def __init__(self,*,models,api_key):
        if not isinstance(models,dict) or not isinstance(models.get('deepseek'),str) or not models['deepseek'] or set(models)-{'deepseek','ollama'}:
            raise ValueError('reviewed paid child model map required')
        if not isinstance(api_key,str) or not api_key.strip():raise ValueError('parent provider key required')
        self.models=dict(models);self._key=api_key

    def require(self,provider,model):
        if self.models.get(provider)!=model:raise PermissionError('child route differs from reviewed parent model')

    def transport(self,provider,model,local_endpoint):
        self.require(provider,model)
        if provider=='ollama':return local_endpoint,{}
        return 'https://api.deepseek.com/chat/completions',{'Authorization':'Bearer '+self._key}

    def compute(self,store,operation,gateway,phase,journal):
        b=operation.binding;self.require(b.provider,b.model)
        if b.provider=='ollama':return None
        if (gateway.store is not store or gateway.job_id!=b.job_id or not gateway.require_prepaid
            or not gateway.launch_guard or gateway.retail_rates is None or not callable(getattr(gateway,'admit_serialized',None))):
            raise PermissionError('paid child requires parent atomic dispatch, exact retail and confirmed tripwires')
        return ChildComputeAccounting(store,operation,rates=gateway.retail_rates,phase=phase,journal=journal)
