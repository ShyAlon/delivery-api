"""Uninstalled parent-owned provider boundary. No endpoint or key from child.

All effects require trusted gateway/journal/client/authorization wiring. A
logical call is recorded before admission; incomplete records pause, never
redispatch. Completed bytes are durable before settlement and child delivery.
"""
import base64
import hashlib
import json
import threading
import uuid
import httpx
from pydantic import BaseModel,ConfigDict,Field,StringConstraints
from typing import Annotated,Literal
from requi_child_contract import Ref

MAX_REQUEST=1024*1024
MAX_RESPONSE=8*1024*1024


class BrokerPaused(RuntimeError):pass


class ProviderCall(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    call_id:Ref
    stage:Ref
    provider:Literal['deepseek','ollama']
    model:Annotated[str,StringConstraints(min_length=1,max_length=200)]
    serialized_request:Annotated[str,StringConstraints(max_length=1400000)]


class ChildProviderBroker:
    def __init__(self,operation,*,gateway,journal,client,endpoint,headers,authorize):
        if gateway.job_id!=operation.binding.job_id:raise ValueError('trusted gateway job mismatch')
        url=httpx.URL(endpoint)
        if url.scheme not in ('http','https') or url.username or url.password or url.query or url.fragment:
            raise ValueError('fixed reviewed provider endpoint required')
        if client.follow_redirects:raise ValueError('provider redirects forbidden')
        self.operation=operation;self.gateway=gateway;self.journal=journal;self.client=client
        self.endpoint=endpoint;self.headers=dict(headers);self.authorize=authorize;self.lock=threading.Lock()

    def dispatch(self,value):
        call=ProviderCall.model_validate(value)
        if (call.provider,call.model)!=(self.operation.binding.provider,self.operation.binding.model):
            raise ValueError('provider/model differs from reviewed operation')
        try:raw=base64.b64decode(call.serialized_request,validate=True)
        except (ValueError,TypeError) as exc:raise ValueError('invalid serialized request') from exc
        if not 0<len(raw)<=MAX_REQUEST:raise ValueError('bounded serialized request required')
        from .provider_request import request_contract
        payload,output_cap=request_contract(raw,call.model)
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
            attempt=None
            try:
                attempt=self.gateway.reserve_serialized(attempt_id=uuid.uuid4().hex,stage=call.stage,
                    provider=call.provider,model=call.model,serialized_request=call.serialized_request,max_output_tokens=output_cap)
                if not attempt:raise BrokerPaused('provider reserve returned no attempt')
                self.journal.bind(key,attempt)
                self.authorize(self.operation,call)  # stop/revocation before start
                self.gateway.start(attempt)
                response=self.client.build_request('POST',self.endpoint,content=raw,headers=self.headers)
                received=self.client.send(response,stream=True,follow_redirects=False)
                try:
                    body=bytearray()
                    for chunk in received.iter_bytes():
                        body.extend(chunk)
                        if len(body)>MAX_RESPONSE:raise BrokerPaused('provider response exceeds bounded durable envelope')
                    if received.status_code!=200:raise BrokerPaused('provider non-success response requires reconciliation')
                    content_type=received.headers.get('content-type','application/json')
                    usage=self._usage(bytes(body),content_type)
                    # Do not persist Set-Cookie, authorization or arbitrary headers.
                    reply={'status':received.status_code,'content_type':content_type,
                        'body':base64.b64encode(body).decode(),'sha256':hashlib.sha256(body).hexdigest()}
                finally:received.close()
                self.journal.complete(key,reply,usage)
            except BaseException as exc:
                if attempt is not None:
                    try:self.gateway.uncertain(attempt)
                    except Exception:pass  # Existing durable dispatched row remains held.
                raise BrokerPaused('provider outcome incomplete; reconcile before retry') from exc
            return self._deliver(self.journal.load(key))

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
