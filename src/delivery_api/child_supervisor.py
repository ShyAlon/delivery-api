"""Opt-in constructor seam. Not installed by the cloud runtime.

The parent authorizes before spawn and fences Stop before signalling. It owns
one active slot until reaped. A reaped process says nothing about provider
outcomes: no execution/money/quiescence state is written by this module.
"""
import asyncio
from dataclasses import dataclass
import os
import json
import signal
import socket
from requi_child_contract import encode_operation,decode_event

import re as _re

# Structured engine timing records (dotted event, key=value fields, session id)
# are retained in the bounded engine stream so the Debug package keeps them.
# Everything else on child stderr keeps the shape-only failure policy: arbitrary
# stderr may contain prompts, parameters or credentials and is never echoed.
_STRUCTURED_TIMING=_re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+(?: [a-z_]+=\S+)*$')
_TIMING_SESSION=_re.compile(r'\bsession=([0-9a-fA-F-]{36})\b')

def _forward_engine_timing(line:str):
    text=line.strip()
    if not text or len(text)>2000 or not _STRUCTURED_TIMING.match(text):return
    found=_TIMING_SESSION.search(text)
    if not found:return
    from .process_logs import emit
    emit('engine',text,session_id=found.group(1))



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

    async def run(self,operation,*,authorize,on_event=lambda event:None,timeout_seconds=3600,stop=None,persist_stop=None,private_config=None,descriptors=None):
        if self.active:raise ChildRunError('single child slot already occupied')
        if stop is not None and persist_stop is None:raise ValueError('durable stop callback required')
        # Callback is supplied by trusted parent wiring, never from the request.
        descriptors=dict(descriptors or {})
        if set(descriptors)-{'broker','compute','output'}:raise ValueError('unknown private child descriptor')
        if any(type(fd) is not int or fd<0 for fd in descriptors.values()):raise ValueError('valid descriptor required')
        raw=None
        if private_config is not None:
            raw=json.dumps(private_config,separators=(',',':'),allow_nan=False).encode()+b'\n'
            if len(raw)>32768:raise ValueError('bounded private launch config required')
        authorize(operation)
        wire=encode_operation(operation)
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        config_parent=config_child=None
        if raw is not None:config_parent,config_child=socket.socketpair()
        self.active=True;self.last_result=None
        process=None;writer=None;stop_task=None;receive_task=None;stderr_task=None
        sequence=0;state=None;child_error=None;failed=False;failure_error=None
        try:
            # Deliberate allowlist: never inherit provider keys, DB URLs, auth
            # headers, cloud credentials, PYTHONPATH, proxy or shell controls.
            env={'LANG':'C.UTF-8','PATH':os.defpath,'REQUI_CHILD_FD':str(child.fileno())}
            fds=[child.fileno(),*descriptors.values()]
            for name,fd in descriptors.items():env['REQUI_CHILD_'+name.upper()+'_FD']=str(fd)
            if config_child is not None:
                fds.append(config_child.fileno());env['REQUI_CHILD_CONFIG_FD']=str(config_child.fileno())
            process=await asyncio.create_subprocess_exec(*self.command,pass_fds=tuple(fds),
                start_new_session=True,env=env,stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
            async def drain_stderr():
                nonlocal child_error
                from .child_failure import parse
                pending=b'';discard=False
                while True:
                    chunk=await process.stderr.read(4096)
                    if not chunk:return
                    for part in chunk.splitlines(keepends=True):
                        if len(pending)+len(part)>4096:discard=True;pending=b''
                        if not discard:pending+=part
                        if part.endswith(b'\n'):
                            if not discard:
                                if child_error is None:child_error=parse(pending)
                                _forward_engine_timing(pending.decode('utf-8',errors='replace'))
                            pending=b'';discard=False
            stderr_task=asyncio.create_task(drain_stderr())
            child.close()
            if config_child is not None:
                config_child.close()
                # Small bounded socket write, no secret in argv/environment/disk.
                config_parent.sendall(raw);config_parent.shutdown(socket.SHUT_WR)
            reader,writer=await asyncio.open_connection(sock=parent,limit=4096)
            writer.write(wire);await writer.drain()
            stop_task=asyncio.create_task(stop.wait()) if stop is not None else None
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
                        try:trailing=await asyncio.wait_for(reader.read(1),self.grace)
                        except asyncio.TimeoutError:
                            # A descendant may inherit the socket/stderr after the
                            # leader exits. Bound EOF wait and reap its private group.
                            await self._reap(process)
                            trailing=await asyncio.wait_for(reader.read(1),self.kill)
                        if trailing:raise ChildRunError('trailing lifecycle data')
                        try:code=await asyncio.wait_for(asyncio.shield(process.wait()),self.grace)
                        except asyncio.TimeoutError:
                            # wait() also waits for inherited stderr pipes; reap
                            # descendants even when the leader already exited.
                            await self._reap(process)
                            code=process.returncode
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
        except Exception as error:
            failed=True;failure_error=error
            from .diagnostic_evidence import bound,failure
            with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                failure('child.lifecycle.failed')
            raise
        finally:
            for task in (receive_task,stop_task):
                if task is not None:
                    if not task.done():task.cancel()
                    try:await task
                    except BaseException:pass
            if writer is not None:writer.close()
            parent.close();child.close()
            for channel in (config_parent,config_child):
                if channel is not None:channel.close()
            try:
                if process is not None:
                    # EOF can arrive just before asyncio observes natural exit.
                    if failed and process.returncode is None:
                        try:await asyncio.wait_for(asyncio.shield(process.wait()),.2)
                        except asyncio.TimeoutError:pass
                    await self._reap(process)
            finally:
                if stderr_task is not None:
                    try:await asyncio.wait_for(stderr_task,.5)
                    except Exception:
                        stderr_task.cancel()
                        try:await stderr_task
                        except BaseException:pass
                if failed and self.last_result is not None:
                    from .diagnostic_evidence import bound,record
                    code=self.last_result.returncode
                    with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                        record('child.exit',pid=self.last_result.pid,returncode=code,
                            signal=-code if code<0 else None,last_state=state,sequence=sequence,
                            termination_scope='observed after supervisor cleanup; a negative code may be cleanup, not original cause')
                        if child_error:
                            record('child.failure.class',error_class=child_error['error_class'])
                            for frame in child_error['frames']:record('child.failure.frame',**frame)
                if failed and self.last_result is not None:
                    # Shape-only stderr evidence: arbitrary stderr may contain
                    # prompts, database parameters or credentials. Never echo it.
                    code=self.last_result.returncode
                    detail='child exit='+str(code)+' signal='+str(-code if code<0 else None)+' last_state='+str(state)+' sequence='+str(sequence)+' termination_scope=observed_after_cleanup'
                    if child_error:
                        detail+=' error_class='+child_error['error_class']+' stderr_frames='+json.dumps(child_error['frames'],separators=(',',':'))
                        if child_error.get('cause_classes'):detail+=' cause_classes='+json.dumps(child_error['cause_classes'],separators=(',',':'))
                    else:detail+=' stderr_shape=unavailable (raw stderr withheld)'
                    import sys
                    print(detail,file=sys.stderr,flush=True)
                    if isinstance(failure_error,ChildRunError):
                        failure_error.args=(str(failure_error)+'; '+detail,)
                self.active=False
        return self.last_result
