"""Uninstalled parent-owned provider boundary. No endpoint or key from child.

All effects require trusted gateway/journal/client/authorization wiring. A
logical call is recorded before admission; incomplete records pause, never
redispatch. Completed bytes are durable before settlement and child delivery.
"""
import base64
import hashlib
import json
import threading
import time
import uuid
import httpx
from pydantic import BaseModel,ConfigDict,Field,StringConstraints
from typing import Annotated,Literal
from requi_child_contract import Ref

MAX_REQUEST=1024*1024
MAX_RESPONSE=8*1024*1024


class BrokerPaused(RuntimeError):pass


class LocalPlanRefused(BrokerPaused):
    """Fixed parent-only pre-dispatch cause, never child-controlled prose."""
    def __init__(self):
        super().__init__('Local model request is incompatible with this execution plan. No provider call was made and no input was truncated. Split this phase or choose a compatible model.')


class ProviderRefused(BrokerPaused):
    """Only parent-generated, sanitized HTTP refusal text may cross to the UI."""
    def __init__(self,error):
        from .provider_http import ProviderHTTPError
        if type(error) is not ProviderHTTPError:raise TypeError('typed parent HTTP refusal required')
        super().__init__(str(error).replace('Review the failed attempt before retrying.',
            'Reconcile this attempt before retrying. Charges may be uncertain.'))



class CallMetadata(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    item:Annotated[str,StringConstraints(min_length=1,max_length=200,pattern=r'^[A-Za-z0-9 _./():-]+$')]|None=None
    fields:Annotated[list[Annotated[str,StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,63}$')]],Field(max_length=32)]=[]


class ProviderCall(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    metadata:CallMetadata|None=None
    call_id:Ref
    stage:Ref
    provider:Literal['deepseek','ollama']
    model:Annotated[str,StringConstraints(min_length=1,max_length=200)]
    serialized_request:Annotated[str,StringConstraints(max_length=1400000)]


class ChildProviderBroker:
    def __init__(self,operation,*,gateway,journal,client,endpoint,headers,authorize):
        if gateway.job_id!=operation.binding.job_id:raise ValueError('trusted gateway job mismatch')
        from .provider_http import ParentProviderHTTP
        self.operation=operation;self.gateway=gateway;self.journal=journal;self.client=client
        self.endpoint=endpoint;self.headers=dict(headers);self.authorize=authorize;self.lock=threading.Lock()
        self.http=ParentProviderHTTP(client,endpoint,headers,local_context=getattr(gateway,'local_context',None));self.timings=[]

    def dispatch(self,value):
        call=ProviderCall.model_validate(value)
        if (call.provider,call.model)!=(self.operation.binding.provider,self.operation.binding.model):
            raise ValueError('provider/model differs from reviewed operation')
        try:raw=base64.b64decode(call.serialized_request,validate=True)
        except (ValueError,TypeError) as exc:raise ValueError('invalid serialized request') from exc
        if not 0<len(raw)<=MAX_REQUEST:raise ValueError('bounded serialized request required')
        from .provider_request import request_contract
        payload,output_cap=request_contract(raw,call.model)
        from .model_registry import native_model
        if call.provider=='ollama' and native_model(call.model):
            from .ollama_native import prepare
            try:prepare(raw,self.http.local_context)
            except ValueError as exc:raise LocalPlanRefused() from exc
        # Buffer terminal SSE too: raw response is durably stored before any
        # bytes are delivered to the child. No blind streaming/retry window.
        if type(payload.get('stream',False)) is not bool:raise ValueError('boolean stream required')
        digest=hashlib.sha256(raw).hexdigest();key=(self.operation.operation_id,call.call_id,call.stage)
        with self.lock:
            self.authorize(self.operation,call)
            prior=self.journal.load(key)
            if prior is not None:
                if (prior['request_sha256'],prior['provider'],prior['model'])!=(digest,call.provider,call.model):
                    raise BrokerPaused('logical provider call conflicts with recorded bytes')
                if prior.get('response') is None or not prior.get('attempt_id'):
                    raise BrokerPaused('incomplete provider evidence; reconcile, never redispatch')
                return self._deliver(prior)
            self.journal.begin(key,digest,call.provider,call.model)
            attempt=None;step='admission'
            try:
                args=dict(attempt_id=uuid.uuid4().hex,stage=call.stage,provider=call.provider,model=call.model,
                    serialized_request=call.serialized_request,max_output_tokens=output_cap)
                if hasattr(self.gateway,'admit_serialized'):
                    attempt=self.gateway.admit_serialized(**args,bind_attempt=lambda aid:self.journal.bind(key,aid),
                        authorize=lambda:self.authorize(self.operation,call))
                else:  # Explicit fake/legacy gateway seam, never production money wiring.
                    attempt=self.gateway.reserve_serialized(**args)
                    if not attempt:raise BrokerPaused('provider reserve returned no attempt')
                    self.journal.bind(key,attempt);self.authorize(self.operation,call);self.gateway.start(attempt)
                step='transport'
                from .provider_http import ProviderHTTPError
                from datetime import datetime,timezone
                started_at=datetime.now(timezone.utc);transport_at=time.monotonic()
                try:body,content_type=self.http.send(raw)
                except ProviderHTTPError as error:
                    from .diagnostic_evidence import record
                    record('child.provider.http_refusal',status=error.status,error_code=error.code,
                        provider=call.provider,model=call.model,reason=str(error))
                    raise
                duration=time.monotonic()-transport_at;finished_at=datetime.now(timezone.utc)
                transport_ms=int(duration*1000)
                step='usage_decode'
                usage=self._usage(body,content_type)
                reply={'status':200,'content_type':content_type,
                    'body':base64.b64encode(body).decode(),'sha256':hashlib.sha256(body).hexdigest()}
                step='journal_complete'
                journal_at=time.monotonic()
                if hasattr(self.journal,'_identity'):
                    # Production journal: completion and safe metadata commit atomically.
                    from .call_metadata import call_evidence
                    from .deepseek_usage import normalize as paid_usage
                    from .ollama_usage import normalize as local_usage
                    try:
                        normalized=(paid_usage if call.provider=='deepseek' else local_usage)(usage)
                        evidence=call_evidence(call,normalized,None,started_at,finished_at,duration)
                        evidence['provider_expense']={'status':'see_provider_attempt_settlement'}
                    except (ValueError,RuntimeError):evidence=None
                    self.journal.complete(key,reply,usage,call_evidence=evidence)
                else:self.journal.complete(key,reply,usage)  # Explicit fake-journal seam.
                journal_ms=int((time.monotonic()-journal_at)*1000)
            except BaseException as exc:
                from .diagnostic_evidence import record
                record('child.broker.step',step=step,attempt_bound=attempt is not None)
                if attempt is not None:
                    from .operational_alerts import emit
                    emit('provider_failure','provider_outcome_incomplete')
                    try:self.gateway.uncertain(attempt)
                    except Exception:pass  # Existing durable dispatched row remains held.
                from .provider_http import ProviderHTTPError
                if type(exc) is ProviderHTTPError:raise ProviderRefused(exc) from exc
                raise BrokerPaused('provider outcome incomplete; reconcile before retry') from exc
            settlement_at=time.monotonic();result=self._deliver(self.journal.load(key))
            self.timings.append({'stage':call.stage,'transport_ms':transport_ms,'journal_ms':journal_ms,'settlement_ms':int((time.monotonic()-settlement_at)*1000)})
            return result

    @staticmethod
    def _usage(body,content_type):
        if 'text/event-stream' not in content_type:
            value=json.loads(body)
            if not isinstance(value,dict) or not isinstance(value.get('usage'),dict):raise BrokerPaused('terminal usage missing')
            return value['usage']
        # Same terminal-choice policy as installed wire wrappers; no content
        # event may provide the authoritative usage. Full framing is buffered.
        import re
        usage=None;done=False
        for line in re.split(b'\r\n|\r|\n',body):
            if len(line)>65536:raise BrokerPaused('oversized provider SSE line')
            if not line.startswith(b'data:'):continue
            data=line[5:].strip()
            if data==b'[DONE]':done=True;continue
            value=json.loads(data)
            if value.get('usage') is not None:
                if usage is not None or not _terminal_choices(value.get('choices')):raise BrokerPaused('ambiguous terminal usage')
                usage=value['usage']
        if not done or not isinstance(usage,dict):raise BrokerPaused('incomplete terminal SSE')
        return usage

    def _deliver(self,row):
        reply=row['response']
        raw=base64.b64decode(reply['body'],validate=True)
        if len(raw)>MAX_RESPONSE or hashlib.sha256(raw).hexdigest()!=reply['sha256']:
            raise BrokerPaused('durable provider response hash differs')
        state=self.gateway._attempt(row['attempt_id'])['state']
        if state=='uncertain':raise BrokerPaused('uncertain provider attempt needs explicit reconciliation')
        if state!='settled':
            try:self.gateway.settle_raw(row['attempt_id'],row['usage'])
            except Exception as exc:raise BrokerPaused('durable response awaits settlement; no redispatch') from exc
        return dict(reply)


def _terminal_choices(choices):
    if choices==[]:return True
    if not isinstance(choices,list) or not choices:return False
    for choice in choices:
        if not isinstance(choice,dict) or choice.get('finish_reason') not in ('stop','length','tool_calls','content_filter','insufficient_system_resource'):return False
        delta=choice.get('delta')
        if not isinstance(delta,dict) or any(value not in (None,'',[],{}) for value in delta.values()):return False
    return True
