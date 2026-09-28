"""Typed HTTP client boundary for remote V-model engines."""
from __future__ import annotations
from abc import ABC,abstractmethod
from uuid import UUID
import httpx
from vmodel_contracts.interactive import (
    EngineSnapshot,InputResponse,ProtocolError,ProtocolErrorCode,StartSessionRequest,
    StartSessionResponse,SubmitInputResponse,ModelRouteSwitchRequest,ModelRouteSwitchResponse,
)


class EngineClientError(RuntimeError):
    def __init__(self,http_status:int,error:ProtocolError):
        super().__init__(error.detail);self.http_status,self.error=http_status,error


class EngineUnreachableError(RuntimeError):
    """The engine service did not accept the connection at all (down, wrong port, crashed)."""
    def __init__(self,base_url:str,cause:httpx.TransportError):
        self.base_url,self.cause=str(base_url).rstrip('/'),cause
        super().__init__(f"interview engine is not reachable at {self.base_url}: {cause}")


class EngineClient(ABC):
    @abstractmethod
    def start(self,request:StartSessionRequest)->StartSessionResponse:...
    @abstractmethod
    def snapshot(self,session_id:str)->EngineSnapshot:...
    @abstractmethod
    def switch_route(self,session_id:str,request:ModelRouteSwitchRequest)->ModelRouteSwitchResponse:
        reply=self._send('POST',f'/v1/sessions/{session_id}/model-route',json=request.model_dump(mode='json'))
        return self._validated(reply,ModelRouteSwitchResponse,200)

    def submit(self,session_id:str,response:InputResponse)->SubmitInputResponse:...


class HttpEngineClient(EngineClient):
    def __init__(self,base_url:str,*,token:str="local-interview-engine-token",timeout:float|None=None,transport:httpx.BaseTransport|None=None):
        if not token:raise ValueError("interview engine service token must not be empty")
        self._http=httpx.Client(base_url=base_url.rstrip('/'),headers={"X-Interview-Engine-Token":token},timeout=timeout,transport=transport)

    def _send(self,method:str,path:str,**kwargs)->httpx.Response:
        # Transport failures (connection refused, timeouts) mean the engine
        # process is not there at all - translate them at this boundary so
        # callers never see a raw httpx traceback.
        try:return self._http.request(method,path,**kwargs)
        except httpx.TransportError as exc:raise EngineUnreachableError(self._http.base_url,exc) from exc

    def default_route(self)->dict:
        response=self._send('GET','/v1/model-routes/default')
        return self._validated(response,dict,200)

    def preview_route(self,route:str,initial_model:str|None=None)->dict:
        response=self._send('GET',f'/v1/model-routes/{route}',params={"initial_model":initial_model} if initial_model else None)
        return self._validated(response,dict,200)

    def start(self,request:StartSessionRequest)->StartSessionResponse:
        response=self._send('POST','/v1/sessions',json=request.model_dump(mode='json'))
        return self._validated(response,StartSessionResponse,201)

    def snapshot(self,session_id:str)->EngineSnapshot:
        response=self._send('GET',f'/v1/sessions/{session_id}')
        return self._validated(response,EngineSnapshot,200)

    def switch_route(self,session_id:str,request:ModelRouteSwitchRequest)->ModelRouteSwitchResponse:
        reply=self._send('POST',f'/v1/sessions/{session_id}/model-route',json=request.model_dump(mode='json'))
        return self._validated(reply,ModelRouteSwitchResponse,200)

    def submit(self,session_id:str,response:InputResponse)->SubmitInputResponse:
        reply=self._send('POST',f'/v1/sessions/{session_id}/inputs/{response.input_id}',json=response.model_dump(mode='json'))
        return self._validated(reply,SubmitInputResponse,200)

    @staticmethod
    def _validated(response:httpx.Response,model:type,expected:int):
        if response.status_code!=expected:
            try:
                body=response.json();payload=body.get('detail',body);error=ProtocolError.model_validate(payload)
            except Exception:
                error=ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR,
                    detail=f'engine returned HTTP {response.status_code} without a valid protocol error')
            raise EngineClientError(response.status_code,error)
        return response.json() if model is dict else model.model_validate(response.json())
