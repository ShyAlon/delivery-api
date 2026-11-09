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

# Observational stage mapping: which engine timing records the operator UI may
# honestly surface, and as which label. A stage appears only when its record
# was observed; nothing here implies work we have not seen.
_TIMING_STAGES={
    'program.construct.begin':('engine.session','Engine starting','started'),
    'staging.ready':('engine.session','Engine starting','started'),
    'saver.setup.begin':('engine.session','Engine loading session','started'),
    'saver.setup.end':('engine.session','Engine loading session','completed'),
    'advance.turn.begin':('engine.turn','Engine working','started'),
    'llm.turn.begin':('engine.llm','Model call running','started'),
    'llm.turn.end':('engine.llm','Model call running','completed'),
    'advance.turn.end':('engine.turn','Finishing','completed'),
    'session.saved':('engine.persist','Session saved','completed'),
}
_TIMING_EVENT=_re.compile(r'^([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)')

def engine_timing_stage(line:str):
    text=line.strip()
    if not text or len(text)>2000 or not _STRUCTURED_TIMING.match(text):return None
    found=_TIMING_SESSION.search(text)
    if not found:return None
    event=_TIMING_EVENT.match(text)
    if not event:return None
    mapped=_TIMING_STAGES.get(event.group(1))
    if mapped is None:return None
    stage,label,status=mapped
    return found.group(1),stage,label,status



class ChildRunError(RuntimeError):pass


class _ChildHandle:
    """A live child and its private channels, parked between operations.
    Lifecycle sequence/state are cumulative across operations on one child."""
    def __init__(self):
        self.process=None;self.parent=None;self.child_sock=None
        self.config_parent=None;self.config_child=None
        self.reader=None;self.writer=None
        self.stderr_task=None;self.receive_task=None;self.stop_task=None
        self.sequence=0;self.state=None;self.child_error=None


@dataclass(frozen=True)
class ChildResult:
    pid:int
    returncode:int
    reaped:bool
    provider_reconciled:bool=False


class ChildSupervisor:
    def __init__(self,command,*,grace_seconds=10,kill_seconds=5,timing_sink=None):
        if not command or any(not isinstance(v,str) or not v for v in command):raise ValueError('fixed trusted command required')
        if grace_seconds<=0 or kill_seconds<=0:raise ValueError('positive stop deadlines required')
        self.command=tuple(command);self.grace=grace_seconds;self.kill=kill_seconds
        self._timing_sink=timing_sink
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

    async def spawn(self,operation,*,authorize,private_config=None,descriptors=None):
        """Authorize, launch the child process and write the first operation.
        No lifecycle events are consumed here; run_operation drives each one."""
        if self.active:raise ChildRunError('single child slot already occupied')
        # Callback is supplied by trusted parent wiring, never from the request.
        descriptors=dict(descriptors or {})
        if set(descriptors)-{'broker','compute','output','progress'}:raise ValueError('unknown private child descriptor')
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
        handle=_ChildHandle();handle.child_sock=child;handle.config_parent=config_parent;handle.config_child=config_child
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
            handle.process=process
            async def drain_stderr():
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
                                if handle.child_error is None:handle.child_error=parse(pending)
                                _forward_engine_timing(pending.decode('utf-8',errors='replace'))
                                if self._timing_sink is not None:
                                    observed=engine_timing_stage(pending.decode('utf-8',errors='replace'))
                                    if observed is not None:self._timing_sink(*observed)
                            pending=b'';discard=False
            handle.stderr_task=asyncio.create_task(drain_stderr())
            child.close();handle.child_sock=None
            if config_child is not None:
                config_child.close();handle.config_child=None
                # Small bounded socket write, no secret in argv/environment/disk.
                config_parent.sendall(raw);config_parent.shutdown(socket.SHUT_WR)
            reader,writer=await asyncio.open_connection(sock=parent,limit=4096)
            handle.parent=parent;handle.reader=reader;handle.writer=writer
            writer.write(wire);await writer.drain()
            return handle
        except Exception as error:
            from .diagnostic_evidence import bound,failure
            with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                failure('child.lifecycle.failed')
            await self.reap(handle,operation,failed=True,failure_error=error)
            raise

    async def _send_frame(self,handle,frame,fds):
        """One sendmsg carrying a warm follow-up frame and its private
        descriptors (SCM_RIGHTS). The writer is drained first, so the frame is
        the next bytes the child reads; asyncio streams cannot sendmsg, so the
        write goes through a short-lived dup of the same drained socket."""
        import array
        rights=array.array('i',[int(fd) for fd in fds])
        dup=socket.socket(fileno=os.dup(handle.parent.fileno()))
        try:
            deadline=asyncio.get_running_loop().time()+self.grace
            while True:
                try:
                    sent=dup.sendmsg([frame],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,rights.tobytes())])
                    if sent!=len(frame):raise ChildRunError('short warm operation frame write')
                    return
                except BlockingIOError:
                    if asyncio.get_running_loop().time()>=deadline:raise ChildRunError('warm operation frame write blocked')
                    await asyncio.sleep(0.01)
        finally:dup.close()

    async def run_operation(self,handle,operation,*,on_event=lambda event:None,timeout_seconds=3600,
            stop=None,persist_stop=None,first=False,park=False,payload=None,fds=()):
        """Drive one operation to its terminal lifecycle event.

        first=True expects the ready handshake; a follow-up expects running.
        park=True returns at the terminal event without waiting for EOF/exit:
        the child stays alive for the next operation, and a forged trailing
        frame can never become success - the next operation's identity and
        cumulative sequence check rejects it."""
        if stop is not None and persist_stop is None:raise ValueError('durable stop callback required')
        if first==(payload is not None):raise ValueError('exactly one of launch wire or follow-up payload required')
        reader=handle.reader;writer=handle.writer;process=handle.process
        if payload is not None:
            frame=json.dumps(payload,separators=(',',':'),allow_nan=False).encode()+b'\n'
            if len(frame)>65536:raise ValueError('bounded warm operation frame required')
            await writer.drain()
            await self._send_frame(handle,frame,fds)
        stop_task=asyncio.create_task(stop.wait()) if stop is not None else None
        handle.stop_task=stop_task
        async def receive():
            while True:
                try:raw=await reader.readline()
                except ValueError as exc:raise ChildRunError('child lifecycle frame exceeds limit') from exc
                if not raw:raise ChildRunError('child descriptor closed before terminal event')
                try:event=decode_event(raw)
                except ValueError as exc:raise ChildRunError('invalid child lifecycle frame') from exc
                if event.operation_id!=operation.operation_id or event.sequence!=handle.sequence+1:
                    raise ChildRunError('child lifecycle identity or sequence changed')
                if first:
                    allowed={None:{'ready'},'ready':{'running','stopped','failed'},'running':{'completed','stopped','failed'}}
                else:
                    # A parked child completed its previous operation; the next
                    # one begins at running. The contract state set is closed,
                    # so these are the only values a follow-up may emit.
                    allowed={'completed':{'running'},'running':{'completed','stopped','failed'}}
                if event.state not in allowed.get(handle.state,set()):raise ChildRunError('invalid child lifecycle transition')
                handle.sequence=event.sequence;handle.state=event.state;on_event(event)
                if event.state in {'completed','stopped','failed'}:
                    if park:return
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
                    if code!=0 or event.state=='failed':raise ChildRunError('child failed; durable reconciliation required')
                    return
        receive_task=asyncio.create_task(receive());handle.receive_task=receive_task
        try:
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
                if task is not None and not task.done():
                    task.cancel()
                    try:await task
                    except BaseException:pass

    async def reap(self,handle,operation,*,failed=False,failure_error=None):
        """Cancel drivers, close channels, reap the process group, drain
        stderr, and free the slot. Safe on a partially-built handle."""
        for task in (handle.receive_task,handle.stop_task):
            if task is not None:
                if not task.done():task.cancel()
                try:await task
                except BaseException:pass
        if handle.writer is not None:handle.writer.close()
        if handle.parent is not None:handle.parent.close()
        if handle.child_sock is not None:handle.child_sock.close()
        for channel in (handle.config_parent,handle.config_child):
            if channel is not None:channel.close()
        process=handle.process
        try:
            if process is not None:
                # EOF can arrive just before asyncio observes natural exit.
                if failed and process.returncode is None:
                    try:await asyncio.wait_for(asyncio.shield(process.wait()),.2)
                    except asyncio.TimeoutError:pass
                await self._reap(process)
        finally:
            if handle.stderr_task is not None:
                try:await asyncio.wait_for(handle.stderr_task,.5)
                except Exception:
                    handle.stderr_task.cancel()
                    try:await handle.stderr_task
                    except BaseException:pass
            if failed and self.last_result is not None:
                from .diagnostic_evidence import bound,record
                code=self.last_result.returncode
                with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                    record('child.exit',pid=self.last_result.pid,returncode=code,
                        signal=-code if code<0 else None,last_state=handle.state,sequence=handle.sequence,
                        termination_scope='observed after supervisor cleanup; a negative code may be cleanup, not original cause')
                    if handle.child_error:
                        record('child.failure.class',error_class=handle.child_error['error_class'])
                        for frame in handle.child_error['frames']:record('child.failure.frame',**frame)
            if failed and self.last_result is not None:
                # Shape-only stderr evidence: arbitrary stderr may contain
                # prompts, database parameters or credentials. Never echo it.
                code=self.last_result.returncode
                detail='child exit='+str(code)+' signal='+str(-code if code<0 else None)+' last_state='+str(handle.state)+' sequence='+str(handle.sequence)+' termination_scope=observed_after_cleanup'
                if handle.child_error:
                    detail+=' error_class='+handle.child_error['error_class']+' stderr_frames='+json.dumps(handle.child_error['frames'],separators=(',',':'))
                    if handle.child_error.get('cause_classes'):detail+=' cause_classes='+json.dumps(handle.child_error['cause_classes'],separators=(',',':'))
                else:detail+=' stderr_shape=unavailable (raw stderr withheld)'
                import sys
                print(detail,file=sys.stderr,flush=True)
                if isinstance(failure_error,ChildRunError):
                    failure_error.args=(str(failure_error)+'; '+detail,)
            self.active=False
        return self.last_result

    async def run(self,operation,*,authorize,on_event=lambda event:None,timeout_seconds=3600,stop=None,persist_stop=None,private_config=None,descriptors=None):
        if stop is not None and persist_stop is None:raise ValueError('durable stop callback required')
        handle=await self.spawn(operation,authorize=authorize,private_config=private_config,descriptors=descriptors)
        error=None
        try:
            await self.run_operation(handle,operation,on_event=on_event,timeout_seconds=timeout_seconds,
                stop=stop,persist_stop=persist_stop,first=True)
        except Exception as exc:
            error=exc
            from .diagnostic_evidence import bound,failure
            with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                failure('child.lifecycle.failed')
            raise
        finally:
            await self.reap(handle,operation,failed=error is not None,failure_error=error)
        return self.last_result

    async def _finish_parked(self,handle,operation,*,signal_stop=False,persist_stop=None):
        """End an idle parked child: optional fenced STOP, then bounded EOF and
        a verified clean exit, then the ordinary reap. A parked child emits
        nothing more; any frame here is a protocol violation."""
        if handle.state!='completed':raise ChildRunError('parked child is not between operations')
        error=None
        try:
            if signal_stop:
                persist_stop(operation)
                handle.writer.write(b'STOP\n')
            else:
                # Recycling without a stop: write-side EOF triggers the child's
                # own lease release and exit.
                handle.parent.shutdown(socket.SHUT_WR)
            await handle.writer.drain()
            trailing=await asyncio.wait_for(handle.reader.read(4096),self.grace)
            if trailing:raise ChildRunError('parked child emitted after its last operation')
            code=await asyncio.wait_for(asyncio.shield(handle.process.wait()),self.grace)
            if code!=0:raise ChildRunError('parked child did not exit cleanly; durable reconciliation required')
        except Exception as exc:
            error=exc
            from .diagnostic_evidence import bound,failure
            with bound(task_id=operation.binding.task_id,session_id=operation.binding.session_id,job_id=operation.binding.job_id):
                failure('child.lifecycle.failed')
            raise
        finally:
            await self.reap(handle,operation,failed=error is not None,failure_error=error)
        return self.last_result

    async def stop_parked(self,handle,operation,*,persist_stop):
        """Stop an idle parked child (owner stop evidence, then STOP)."""
        return await self._finish_parked(handle,operation,signal_stop=True,persist_stop=persist_stop)

    async def close_parked(self,handle,operation):
        """Recycle an idle parked child without a stop fence (write-side EOF)."""
        return await self._finish_parked(handle,operation)
