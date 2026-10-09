"""In-process private worker adapter; transport and price/bound resolver unwired.

Resolver must serialize the COMPLETE provider request and return a reviewed quote
and exact request digest. It runs in trusted delivery code, not producer input.
Missing resolver/usage fails closed. No monetary configuration defaults exist.
"""
from .attempt_ledger import AdmissionDenied

class WorkerAdmissionGateway:
    def __init__(self,store,job_id,*,resolve_quote=None,measure_usage=None,require_prepaid=True,resolve_serialized=None,measure_raw_usage=None):
        self.store=store;self.job_id=job_id;self.require_prepaid=require_prepaid
        self.resolve_quote=resolve_quote;self.measure_usage=measure_usage
        self.resolve_serialized=resolve_serialized;self.measure_raw_usage=measure_raw_usage

    def reserve(self,*,attempt_id,stage,provider,model,messages,max_output_tokens):
        if self.resolve_quote is None:raise AdmissionDenied('reviewed request bound/price resolver unavailable')
        quote,digest=self.resolve_quote(stage=stage,provider=provider,model=model,
            messages=messages,max_output_tokens=max_output_tokens)
        if quote['provider']!=provider or quote['model']!=model or quote['output_max']!=max_output_tokens:
            raise AdmissionDenied('resolved quote does not match requested attempt')
        return self.store.reserve_provider_attempt(self.job_id,attempt_id,quote,digest,require_prepaid=self.require_prepaid)['attempt_id']

    def reserve_serialized(self,*,attempt_id,stage,provider,model,serialized_request,max_output_tokens):
        import base64,hashlib,json
        if self.resolve_serialized is None:raise AdmissionDenied('serialized request resolver unavailable')
        try:raw=base64.b64decode(serialized_request,validate=True)
        except (ValueError,TypeError) as exc:raise AdmissionDenied('invalid serialized request') from exc
        try:
            payload=json.loads(raw)
            caps=[payload[k] for k in ('max_tokens','max_completion_tokens') if k in payload]
            if payload.get('model')!=model or len(caps)!=1 or type(caps[0]) is not int or caps[0]!=max_output_tokens:raise ValueError()
        except (ValueError,TypeError,AttributeError) as exc:raise AdmissionDenied('serialized request identity/cap mismatch') from exc
        quote,digest=self.resolve_serialized(stage=stage,provider=provider,model=model,
            serialized_request=raw,max_output_tokens=max_output_tokens)
        if quote['provider']!=provider or quote['model']!=model or quote['output_max']!=max_output_tokens or digest!=hashlib.sha256(raw).hexdigest():
            raise AdmissionDenied('serialized quote/digest mismatch')
        return self.store.reserve_provider_attempt(self.job_id,attempt_id,quote,digest,require_prepaid=self.require_prepaid)['attempt_id']

    def settle_raw(self,attempt,raw_usage):
        self._attempt(attempt)
        self.store.record_raw_provider_usage(attempt,raw_usage)
        try:usage=self.measure_raw_usage(raw_usage) if self.measure_raw_usage else None
        except Exception:usage=None  # Evidence survives a failed schema/rate adapter.
        result=self.store.transition_provider_attempt(attempt,'settle',usage)
        if result['state']!='settled':raise AdmissionDenied('raw usage uncertain; reservation held for review')

    def _attempt(self,attempt):
        row=self.store.provider_attempt(attempt)
        if not row or row['job_id']!=self.job_id:
            raise AdmissionDenied('attempt does not belong to this worker job')
        return row

    def start(self,attempt):
        self._attempt(attempt)
        self.store.transition_provider_attempt(attempt,'start',require_prepaid=self.require_prepaid)

    def settle(self,attempt,response):
        self._attempt(attempt)
        usage=self.measure_usage(response) if self.measure_usage else None
        result=self.store.transition_provider_attempt(attempt,'settle',usage)
        if result['state']!='settled':raise AdmissionDenied('usage uncertain; reservation held for review')

    def uncertain(self,attempt):
        row=self._attempt(attempt)
        if row['state']=='dispatched':self.store.transition_provider_attempt(attempt,'uncertain')


# Runner boundary: a producer lacking the guarded admission protocol must never
# start merely because a route used a test-only gate override.
from contextvars import ContextVar
from contextlib import contextmanager
_active_gateway=ContextVar('active_worker_admission_gateway',default=None)

@contextmanager
def worker_gateway(gateway):
    token=_active_gateway.set(gateway)
    try:yield
    finally:_active_gateway.reset(token)

def current_worker_gateway(store,task_id):
    gateway=_active_gateway.get()
    if gateway is None:raise AdmissionDenied('cloud subprocess admission gateway not configured')
    job=store.worker_job(gateway.job_id)
    if not job or job['task_id']!=task_id or job['state']!='running':
        raise AdmissionDenied('cloud subprocess requires matching running worker job')
    if gateway.resolve_quote is None or gateway.measure_usage is None:
        raise AdmissionDenied('cloud subprocess reviewed bounds/pricing/usage unavailable')
    return gateway
