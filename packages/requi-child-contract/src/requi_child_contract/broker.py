"""Private parent-broker HTTP client. Never opens a provider network socket.

No retry/reconnect, child price, URL, credential, settle or release authority.
The fixed runner supplies stable logical call IDs from durable execution state.
"""
import asyncio
import base64
import hashlib
import json
import os
import socket
import threading
import httpx

MAX_FRAME=12*1024*1024


class BrokerUnavailable(RuntimeError):pass


class BrokerClient:
    def __init__(self,fd,*,next_call_id):
        self.socket=socket.socket(fileno=os.dup(fd));self.socket.settimeout(3600)
        self.stream=self.socket.makefile('rwb');self.lock=threading.Lock();self.next_call_id=next_call_id;self.failed=False

    def request(self,*,stage,provider,model,raw):
        with self.lock:
            if self.failed:raise BrokerUnavailable('parent broker channel already lost')
            try:
                body={'call_id':self.next_call_id(stage),'stage':stage,'provider':provider,'model':model,
                    'serialized_request':base64.b64encode(raw).decode()}
                frame=json.dumps(body,separators=(',',':')).encode()+b'\n'
                if len(frame)>2*1024*1024:raise ValueError('oversized broker request')
                from .compute import excluded_wait
                with excluded_wait('provider:'+body['call_id']):
                    self.stream.write(frame);self.stream.flush()
                    reply=self.stream.readline(MAX_FRAME+1)
                if not reply.endswith(b'\n') or len(reply)>MAX_FRAME:raise ValueError('bounded broker response required')
                from . import _unique_pairs,_invalid_constant
                value=json.loads(reply,object_pairs_hook=_unique_pairs,parse_constant=_invalid_constant)
                if set(value)!={'status','content_type','body','sha256'} or value['status']!=200:raise ValueError('invalid broker response')
                raw=base64.b64decode(value['body'],validate=True)
                if len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=value['sha256']:raise ValueError('broker response hash mismatch')
                return value['status'],value['content_type'],raw
            except Exception as exc:
                self.failed=True
                raise BrokerUnavailable('parent provider broker unavailable; reconcile, never retry') from exc

    def close(self):self.stream.close();self.socket.close()


class BrokerTransport(httpx.BaseTransport):
    def __init__(self,broker,*,stage,provider,model):self.broker=broker;self.stage=stage;self.provider=provider;self.model=model
    def handle_request(self,request):
        if request.method!='POST' or not request.url.path.endswith('/chat/completions'):raise BrokerUnavailable('unsupported broker SDK operation')
        status,content_type,raw=self.broker.request(stage=self.stage,provider=self.provider,model=self.model,raw=request.read())
        return httpx.Response(status,headers={'content-type':content_type},content=raw,request=request)


class AsyncBrokerTransport(httpx.AsyncBaseTransport):
    def __init__(self,broker,*,stage,provider,model):self.broker=broker;self.stage=stage;self.provider=provider;self.model=model
    async def handle_async_request(self,request):
        if request.method!='POST' or not request.url.path.endswith('/chat/completions'):raise BrokerUnavailable('unsupported broker SDK operation')
        body=await request.aread()
        status,content_type,raw=await asyncio.to_thread(self.broker.request,stage=self.stage,provider=self.provider,model=self.model,raw=body)
        return httpx.Response(status,headers={'content-type':content_type},content=raw,request=request)


def clients(broker,*,stage,provider,model):
    args={'stage':stage,'provider':provider,'model':model}
    return (httpx.Client(transport=BrokerTransport(broker,**args),trust_env=False,follow_redirects=False),
        httpx.AsyncClient(transport=AsyncBrokerTransport(broker,**args),trust_env=False,follow_redirects=False))
