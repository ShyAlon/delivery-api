"""Opt-in constructor seam. Not installed by the cloud runtime.

The parent authorizes before spawn and fences Stop before signalling. It owns
one active slot until reaped. A reaped process says nothing about provider
outcomes: no execution/money/quiescence state is written by this module.
"""
import asyncio
from dataclasses import dataclass
import os
import signal
import socket
from requi_child_contract import encode_operation,decode_event


class ChildRunError(RuntimeError):pass


@dataclass(frozen=True)
class ChildResult:
    pid:int
    returncode:int
    reaped:bool
    provider_reconciled:bool=False


class ChildSupervisor:
    def __init__(self,command,*,grace_seconds=10,kill_seconds=5):
        if not command or any(not isinstance(v,str) or not v for v in command):raise ValueError('fixed trusted command required')
        if grace_seconds<=0 or kill_seconds<=0:raise ValueError('positive stop deadlines required')
        self.command=tuple(command);self.grace=grace_seconds;self.kill=kill_seconds
        self.active=False;self.last_result=None

    async def _reap(self,process):
        # Reap the leader AND kill any surviving descendants in its private group.
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),self.kill)
            except asyncio.TimeoutError:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        self.last_result=ChildResult(process.pid,process.returncode,True)

    async def run(self,operation,*,authorize,on_event=lambda event:None,timeout_seconds=3600,stop=None,persist_stop=None):
        if self.active:raise ChildRunError('single child slot already occupied')
        if stop is not None and persist_stop is None:raise ValueError('durable stop callback required')
        # Callback is supplied by trusted parent wiring, never from the request.
        authorize(operation)
        wire=encode_operation(operation)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        self.active=True
        process=None;writer=None;stop_task=None;receive_task=None
        try:
            # Deliberate allowlist: never inherit provider keys, DB URLs, auth
            # headers, cloud credentials, PYTHONPATH, proxy or shell controls.
            env={'LANG':'C.UTF-8','PATH':os.defpath,'REQUI_CHILD_FD':str(child.fileno())}
            process=await asyncio.create_subprocess_exec(*self.command,pass_fds=(child.fileno(),),
                start_new_session=True,env=env,stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            child.close()
            reader,writer=await asyncio.open_connection(sock=parent,limit=4096)
            writer.write(wire);await writer.drain()
            stop_task=asyncio.create_task(stop.wait()) if stop is not None else None
            sequence=0;state=None
            async def receive():
                nonlocal sequence,state
                while True:
                    try:raw=await reader.readline()
                    except ValueError as exc:raise ChildRunError('child lifecycle frame exceeds limit') from exc
                    if not raw:raise ChildRunError('child descriptor closed before terminal event')
                    try:event=decode_event(raw)
                    except ValueError as exc:raise ChildRunError('invalid child lifecycle frame') from exc
                    if event.operation_id!=operation.operation_id or event.sequence!=sequence+1:
                        raise ChildRunError('child lifecycle identity or sequence changed')
                    allowed={None:{'ready'},'ready':{'running','stopped','failed'},'running':{'completed','stopped','failed'}}
                    if event.state not in allowed.get(state,set()):raise ChildRunError('invalid child lifecycle transition')
                    sequence=event.sequence;state=event.state;on_event(event)
                    if state in {'completed','stopped','failed'}:
                        # Require EOF and exit: no duplicate or trailing frame can
                        # turn an apparently completed operation into success.
                        if await reader.read(1):raise ChildRunError('trailing lifecycle data')
                        code=await process.wait()
                        if code!=0 or state=='failed':raise ChildRunError('child failed; durable reconciliation required')
                        return
            receive_task=asyncio.create_task(receive())
            done,_=await asyncio.wait([receive_task]+([stop_task] if stop_task else []),timeout=timeout_seconds,return_when=asyncio.FIRST_COMPLETED)
            if not done:raise ChildRunError('child deadline exceeded; no automatic replay')
            if stop_task in done:
                persist_stop(operation)  # Failure here still reaps, never signals before fence.
                writer.write(b'STOP\n');await writer.drain()
                try:await asyncio.wait_for(asyncio.shield(receive_task),self.grace)
                except asyncio.TimeoutError:raise ChildRunError('child did not stop cooperatively')
            else:await receive_task
        finally:
            for task in (receive_task,stop_task):
                if task is not None:
                    if not task.done():task.cancel()
                    try:await task
                    except BaseException:pass
            if writer is not None:writer.close()
            parent.close();child.close()
            try:
                if process is not None:await self._reap(process)
            finally:self.active=False
        return self.last_result
