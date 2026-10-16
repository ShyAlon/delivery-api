"""In-process private worker adapter; transport and price/bound resolver unwired.

Resolver must serialize the COMPLETE provider request and return a reviewed quote
and exact request digest. It runs in trusted delivery code, not producer input.
Missing resolver/usage fails closed. No monetary configuration defaults exist.
"""
from .attempt_ledger import AdmissionDenied
from .tenancy import current_scope

class WorkerAdmissionGateway:
    def __init__(self,store,job_id,*,resolve_quote=None,measure_usage=None,require_prepaid=True,resolve_serialized=None,measure_raw_usage=None,launch_guard=False,retail_rates=None,verify_provider_snapshot=None,provider_balance=None):
        if launch_guard and not require_prepaid:raise AdmissionDenied('launch guard requires prepaid accounting')
        self.provider_balance=provider_balance
        self.verify_provider_snapshot=verify_provider_snapshot
        self.retail_rates=retail_rates
        self.compute_meter=None;self._meter_attempts=set()
        self.launch_guard=launch_guard
        self.store=store;self.job_id=job_id;self.require_prepaid=require_prepaid
        self.resolve_quote=resolve_quote;self.measure_usage=measure_usage
        self.resolve_serialized=resolve_serialized;self.measure_raw_usage=measure_raw_usage

    def record_wire_evidence(self,attempt,evidence):
        import json
        row=self._attempt(attempt)
        allowed={'status','response_sha256','response_bytes','cr_count','lf_count','event_count','terminal_usage','done_seen','error_class'}
        if set(evidence)!=allowed or len(json.dumps(evidence))>65536:raise AdmissionDenied('bounded exact wire evidence required')
        if evidence['status'] not in ('settled','failed','closed_without_settlement','early_close') or (evidence['error_class'] is not None and (not isinstance(evidence['error_class'],str) or not evidence['error_class'].isidentifier() or len(evidence['error_class'])>100)):raise AdmissionDenied('bounded wire status/error class required')
        usage=evidence['terminal_usage']
        keys={'prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','prompt_tokens_details','completion_tokens_details'}
        details={'cached_tokens','audio_tokens','reasoning_tokens','accepted_prediction_tokens','rejected_prediction_tokens'}
        if usage is not None:
            if not isinstance(usage,dict) or not set(usage)<=keys:raise AdmissionDenied('token-only wire usage required')
            for key,value in usage.items():
                if key.endswith('_details'):
                    if not isinstance(value,dict) or not set(value)<=details or any(type(n) is not int or n<0 for n in value.values()):raise AdmissionDenied('token-only wire details required')
                elif type(value) is not int or value<0:raise AdmissionDenied('exact wire token counts required')
        digest=evidence['response_sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise AdmissionDenied('exact response hash required')
        if any(type(evidence[k]) is not int or evidence[k]<0 for k in ('response_bytes','cr_count','lf_count','event_count')) or type(evidence['done_seen']) is not bool:raise AdmissionDenied('exact wire counts required')
        with self.store._postgres.transaction():
            self.store.audit(current_scope().owner_id,'provider.wire.evidence',self.store.worker_job(self.job_id)['task_id'],{'attempt_id':attempt,'job_id':self.job_id,'evidence':evidence})

    def _verify_snapshot(self):
        if self.verify_provider_snapshot is not None:
            from .compute_meter import excluded_wait
            try:
                with excluded_wait():self.verify_provider_snapshot()
            except AdmissionDenied:raise
            except Exception as exc:
                # A plain error would reach the model client as a generic "Connection error"; name it.
                raise AdmissionDenied('vendor source check failed: '+str(exc)[:300]) from exc

    def _phase_model(self,provider,model):
        job=self.store.worker_job(self.job_id)
        options=(job or {}).get('execution_options') or {}
        selected=options.get('model_route')
        if selected and selected!=provider+':'+model:
            raise AdmissionDenied('provider model differs from durable user phase selection')

    def reserve(self,*,attempt_id,stage,provider,model,messages,max_output_tokens):
        self._phase_model(provider,model)
        if self.resolve_quote is None:raise AdmissionDenied('reviewed request bound/price resolver unavailable')
        self._verify_snapshot()
        quote,digest=self.resolve_quote(stage=stage,provider=provider,model=model,
            messages=messages,max_output_tokens=max_output_tokens)
        if quote['provider']!=provider or quote['model']!=model or quote['output_max']!=max_output_tokens:
            raise AdmissionDenied('resolved quote does not match requested attempt')
        return self.store.reserve_provider_attempt(self.job_id,attempt_id,quote,digest,require_prepaid=self.require_prepaid)['attempt_id']

    def reserve_serialized(self,*,attempt_id,stage,provider,model,serialized_request,max_output_tokens):
        import base64,hashlib,json
        self._phase_model(provider,model)
        if self.resolve_serialized is None:raise AdmissionDenied('serialized request resolver unavailable')
        try:raw=base64.b64decode(serialized_request,validate=True)
        except (ValueError,TypeError) as exc:raise AdmissionDenied('invalid serialized request') from exc
        try:
            payload=json.loads(raw)
            caps=[payload[k] for k in ('max_tokens','max_completion_tokens') if k in payload]
            if payload.get('model')!=model or len(caps)!=1 or type(caps[0]) is not int or caps[0]!=max_output_tokens:raise ValueError()
        except (ValueError,TypeError,AttributeError) as exc:raise AdmissionDenied('serialized request identity/cap mismatch') from exc
        existing=self.store.provider_attempt(attempt_id)
        if existing:
            quote=existing['quote'];digest=hashlib.sha256(raw).hexdigest()
            if existing['job_id']!=self.job_id or quote['provider']!=provider or quote['model']!=model or quote['output_max']!=max_output_tokens:
                raise AdmissionDenied('existing attempt identity differs from request')
            return self.store.reserve_provider_attempt(self.job_id,attempt_id,quote,digest,require_prepaid=self.require_prepaid)['attempt_id']
        self._verify_snapshot()
        quote,digest=self.resolve_serialized(stage=stage,provider=provider,model=model,
            serialized_request=raw,max_output_tokens=max_output_tokens)
        if quote['provider']!=provider or quote['model']!=model or quote['output_max']!=max_output_tokens or digest!=hashlib.sha256(raw).hexdigest():
            raise AdmissionDenied('serialized quote/digest mismatch')
        return self.store.reserve_provider_attempt(self.job_id,attempt_id,quote,digest,require_prepaid=self.require_prepaid)['attempt_id']

    def settle_raw(self,attempt,raw_usage):
        self._attempt(attempt)
        self.store.record_raw_provider_usage(attempt,raw_usage)
        try:self._verify_snapshot()
        except Exception:
            self.uncertain(attempt)
            raise AdmissionDenied('vendor source unverifiable/changed; accepted usage held') from None
        try:usage=self.measure_raw_usage(raw_usage) if self.measure_raw_usage else None
        except Exception:usage=None  # Evidence survives a failed schema/rate adapter.
        result=self.store.transition_provider_attempt(attempt,'settle',usage)
        self._resume_meter(attempt)
        if result['state']!='settled':raise AdmissionDenied('raw usage uncertain; reservation held for review')

    def _resume_meter(self,attempt):
        if attempt in self._meter_attempts:
            self._meter_attempts.remove(attempt);self.compute_meter.resume()

    def _attempt(self,attempt):
        row=self.store.provider_attempt(attempt)
        if not row or row['job_id']!=self.job_id:
            raise AdmissionDenied('attempt does not belong to this worker job')
        return row

    def start(self,attempt):
        row=self._attempt(attempt)
        # Slow vendor-source verification runs first so the 30s balance freshness window
        # covers only the balance read to the start transition.
        self._verify_snapshot()
        if 'estimate_policy' in row.get('quote',{}):
            if self.provider_balance is None:raise AdmissionDenied('pre-call provider balance reader required')
            from .compute_meter import excluded_wait
            try:
                with excluded_wait():balance=self.provider_balance()
            except AdmissionDenied:raise
            except Exception as exc:raise AdmissionDenied('provider balance read failed: '+str(exc)[:300]) from exc
            self.store.record_provider_balance_check(attempt,balance)
        if self.launch_guard:
            result=self.store.start_launch_provider_attempt(attempt)
            if not result['started']:raise AdmissionDenied('service temporarily unavailable: '+result['reason'])
        else:self.store.transition_provider_attempt(attempt,'start',require_prepaid=self.require_prepaid)
        from .compute_meter import current
        meter=self.compute_meter or current()
        if meter:
            self.compute_meter=meter
            self.compute_meter.pause();self._meter_attempts.add(attempt)

    def settle(self,attempt,response):
        self._attempt(attempt)
        try:self._verify_snapshot()
        except Exception:
            self.uncertain(attempt)
            raise AdmissionDenied('vendor source unverifiable/changed; accepted usage held') from None
        usage=self.measure_usage(response) if self.measure_usage else None
        result=self.store.transition_provider_attempt(attempt,'settle',usage)
        self._resume_meter(attempt)
        if result['state']!='settled':raise AdmissionDenied('usage uncertain; reservation held for review')

    def uncertain(self,attempt):
        row=self._attempt(attempt)
        if row['state']=='dispatched':self.store.transition_provider_attempt(attempt,'uncertain')
        self._resume_meter(attempt)


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
    if gateway.retail_rates is not None:
        raise AdmissionDenied('exact retail subprocess compute metering is not installed; dispatch held')
    if gateway.resolve_quote is None or gateway.measure_usage is None:
        raise AdmissionDenied('cloud subprocess reviewed bounds/pricing/usage unavailable')
    return gateway
