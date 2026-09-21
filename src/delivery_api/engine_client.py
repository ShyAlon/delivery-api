"""Typed HTTP client boundary for remote V-model engines."""
from __future__ import annotations
from abc import ABC,abstractmethod
from uuid import UUID
import httpx
from vmodel_contracts.interactive import (
    EngineSnapshot,InputResponse,ProtocolError,ProtocolErrorCode,StartSessionRequest,
    StartSessionResponse,SubmitInputResponse,
)


class EngineClientError(RuntimeError):
    def __init__(self,http_status:int,error:ProtocolError):
        super().__init__(error.detail);self.http_status,self.error=http_status,error


class EngineClient(ABC):
    @abstractmethod
    def start(self,request:StartSessionRequest)->StartSessionResponse:...
    @abstractmethod
    def snapshot(self,session_id:str)->EngineSnapshot:...
    @abstractmethod
    def submit(self,session_id:str,response:InputResponse)->SubmitInputResponse:...


class HttpEngineClient(EngineClient):
    def __init__(self,base_url:str,*,token:str="local-interview-engine-token",timeout:float|None=None,transport:httpx.BaseTransport|None=None):
        if not token:raise ValueError("interview engine service token must not be empty")
        self._http=httpx.Client(base_url=base_url.rstrip('/'),headers={"X-Interview-Engine-Token":token},timeout=timeout,transport=transport)

    def start(self,request:StartSessionRequest)->StartSessionResponse:
        response=self._http.post('/v1/sessions',json=request.model_dump(mode='json'))
        return self._validated(response,StartSessionResponse,201)

    def snapshot(self,session_id:str)->EngineSnapshot:
        response=self._http.get(f'/v1/sessions/{session_id}')
        return self._validated(response,EngineSnapshot,200)

    def submit(self,session_id:str,response:InputResponse)->SubmitInputResponse:
        reply=self._http.post(f'/v1/sessions/{session_id}/inputs/{response.input_id}',json=response.model_dump(mode='json'))
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
        return model.model_validate(response.json())
