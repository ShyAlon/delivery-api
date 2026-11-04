"""HTTP lifecycle slice for the exact service image against owned local fixtures.

The workflow supplies a fictional signed token over stdin, never an argument or
receipt. No production behavior changes, identity bypasses, paid routes or retry
of state-changing requests. This slice alone cannot issue an image receipt.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class AcceptanceFailure(RuntimeError):
    pass


class API:
    def __init__(self, base: str, token: str):
        parsed=urlsplit(base)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
            raise AcceptanceFailure('explicit loopback fixture service origin required')
        self.base=base.rstrip('/');self.token=token
    def __call__(self,method,path,body=None,**kwargs):
        if not path.startswith('/api/') or '\n' in path or '://' in path:
            raise AcceptanceFailure('fixed application API path required')
        raw=json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(self.base+path,method=method,data=raw,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Origin':self.base})
        try:
            with urllib.request.urlopen(request,timeout=kwargs.get('timeout_seconds',60)) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise AcceptanceFailure('fixture HTTP '+method+' returned '+str(error.code)+'; no state-changing retry') from None
        except (OSError,ValueError):
            raise AcceptanceFailure('fixture HTTP response unavailable; outcome unconfirmed, no state-changing retry') from None


def owner_module():
    path=Path(__file__).resolve().parents[1]/'owner/deepseek-acceptance.py'
    spec=importlib.util.spec_from_file_location('same_owner_acceptance',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def http_slice(api,*,wait=None):
    """Use the same current-input wait/answer protocol as owner acceptance."""
    owner=owner_module();wait=wait or owner.wait_question
    # A signed data read, not anonymous health, must pass first.
    api('GET','/api/tasks')
    task=api('POST','/api/tasks',{'title':'Owned exact-image fixture','brief':owner.IDEA})
    tid=task.get('task_id')
    if not isinstance(tid,str) or not tid:raise AcceptanceFailure('fixture task identity missing')
    api('POST','/api/tasks/'+tid+'/start',{'model_route':'ollama'})
    first=wait(api,tid,timeout=120)
    control=first['control_snapshot']
    input_id=control.get('input_id')
    sequence=control.get('precondition',{}).get('session_sequence')
    if not input_id or type(sequence) is not int:
        raise AcceptanceFailure('current input identity/sequence missing; no answer sent')
    api('POST','/api/tasks/'+tid+'/answer',{'answer':owner.ANSWER,'decision_id':input_id,'expected_sequence':sequence})
    second=wait(api,tid,after=input_id,timeout=120)
    if second['control_snapshot'].get('input_id')==input_id:
        raise AcceptanceFailure('answer did not advance the retained input')
    deadline=time.monotonic()+60
    while True:
        cost=api('GET','/api/tasks/'+tid+'/cost')
        evidence=cost.get('evidence',{})
        if evidence.get('complete') is True and not any(evidence.get(k) for k in ('active_worker','provider_pending','compute_pending')):break
        if time.monotonic()>=deadline:raise AcceptanceFailure('fixture accounting did not settle')
        time.sleep(.25)
    stopped=api('POST','/api/tasks/'+tid+'/stop')
    if stopped.get('state')!='stopped' or stopped.get('delete_allowed') is not True:
        raise AcceptanceFailure('fixture cooperative Stop not confirmed')
    # Only scoped reads after Stop. Retain content until owned sidecar teardown;
    # absence of live deletion is intentional, not a production purge shortcut.
    retained=api('GET','/api/tasks/'+tid)
    if retained.get('task_id')!=tid:raise AcceptanceFailure('retained stopped task missing')
    return {'task_id':tid,'signed-http':'passed','interview-start-answer-stop':'passed'}
