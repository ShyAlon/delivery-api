"""Subprocess job control for the existing CLIs; no pipeline logic lives here."""
from __future__ import annotations

import asyncio
import hashlib
import codecs
import json
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from .store import PHASES, Storage
from .observability import event as activity

_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named [\'\"]([^\'\"]+)[\'\"]")

def decision_fingerprint(decision: dict) -> str:
    """Stable identity for one exact typed decision occurrence across restart."""
    return hashlib.sha256(json.dumps(decision,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
_PROGRESS = re.compile(r"(?:stage '([^']+)' call|([0-9]+)s elapsed|([0-9]+)/([0-9]+))", re.I)
_PHASE_HINTS = {
    "conops": "conops", "concept option": "conops", "operational scenario": "conops",
    "interview": "intent", "distill": "intent", "intent": "intent", "coverage": "intent",
    "stakeholder need": "intent", "scope": "intent", "requirement preview": "intent",
    # Only these explicit labels belong to later executables. Interview-engine
    # preview prose must never paint downstream SRS/spec/test phases.
    "stage 'srs'": "srs", "stage 'architecture'": "architecture",
    "stage 'items'": "specs", "stage 'tests'": "tests", "stage 'verification'": "tests",
}

@dataclass(frozen=True, slots=True)
class ActiveWork:
    owner: str
    phase: str
    status: str
    started_at: float
    decision: dict | None = None
    durable_ref: str | None = None
    requirement: dict | None = None

    def input_state(self) -> dict:
        return {"waiting":self.status=="waiting", "process_live":self.status in ("running","waiting"), "decision":self.decision, "requirement":self.requirement}


@dataclass
class ProcessRunner:
    store: Storage
    processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    waiting: set[str] = field(default_factory=set)
    interrupted_for_shutdown: set[str] = field(default_factory=set)
    decisions: dict[str, asyncio.Future] = field(default_factory=dict)
    pending_decisions: dict[str, dict] = field(default_factory=dict)
    active_work: dict[str, ActiveWork] = field(default_factory=dict)
    pricing_wait_started: dict[str,float] = field(default_factory=dict)
    pricing_wait_seconds: dict[str,float] = field(default_factory=dict)
    pricing_usage_cursor: dict[str,int] = field(default_factory=dict)
    pricing_llm_cursor: dict[str,int] = field(default_factory=dict)
    pricing_wall_cursor: dict[str,float] = field(default_factory=dict)
    pricing_totals: dict[str,dict] = field(default_factory=dict)
    pricing_paths: dict[str,tuple[Path,Path]] = field(default_factory=dict)
    on_complete: object | None = None

    def _phase(self, text: str, fallback: str) -> str:
        lower = text.lower()
        return next((phase for word, phase in sorted(_PHASE_HINTS.items(), key=lambda item: -len(item[0])) if word in lower), fallback)

    async def run(self, task_id: str, command: list[str], *, cwd: Path, phase: str = "intent",
                  env: dict[str, str] | None = None, completion_phases: tuple[str, ...] = (), infer_phase: bool = True,
                  replay_decisions: dict[str, dict[str, str]] | None = None, stall_timeout: float | None = None) -> int:
        if task_id in self.processes:
            raise RuntimeError("task already has a live process")
        launched_phase=phase
        run_started=time.perf_counter()
        out_index=command.index("--out")+1 if "--out" in command else None
        artifact_dir=Path(command[out_index]) if out_index else None
        usage_path=artifact_dir/"usage.jsonl" if artifact_dir else None
        llm_path=artifact_dir/"llm-calls.jsonl" if artifact_dir else None
        from .pricing import line_count
        usage_start=line_count(usage_path) if usage_path else 0;llm_start=line_count(llm_path) if llm_path else 0
        self.store.update_phase(task_id, phase, "running", detail="Starting engine")
        self.store.add_event(task_id, "command", "Engine process started", phase_key=phase,
                             payload={"argv": command, "cwd": str(cwd)})
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env={**os.environ, **(env or {})},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        self.processes[task_id] = proc
        self.active_work[task_id]=ActiveWork("subprocess",phase,"running",time.time(),durable_ref=str(proc.pid))
        traceback_started = False
        missing_module: str | None = None
        cli_error: str | None = None
        prompt_lines: list[str] = []
        line_buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        continuation_phase: str | None = None
        replay_decisions=dict(replay_decisions or {})
        replaying_prompt=False
        typed_prompt_pending=False
        self.pricing_wait_seconds[task_id]=0.0
        if stall_timeout is None:
            try: stall_timeout=float(os.environ.get("REQUI_RE_RUN_STALL_TIMEOUT","") or 600)
            except ValueError: stall_timeout=600
        stalled=False
        structured_progress_seen=False

        # The child pipeline owns progress and LLM heartbeats. Its lines name
        # the in-flight stage and elapsed time. A second generic heartbeat here
        # used to overwrite that useful state with "Engine is still working".
        try:
            assert proc.stdout
            # Read chunks, not lines: input() prompts normally have no trailing newline.
            # Chunk reads expose the question immediately instead of making the browser
            # look frozen while the engine waits for stdin.
            while True:
                # A child that stops producing output and never exits used to
                # block this loop forever, freezing the task (and the test
                # suite) with no visible error. Bound the silence: kill a
                # stalled child and fail the phase loudly instead.
                try: raw = await asyncio.wait_for(proc.stdout.read(512), stall_timeout)
                except asyncio.TimeoutError:
                    stalled=True
                    activity("engine.stalled","Engine produced no output; stopping the stalled run",task_id=task_id,phase=phase,silent_seconds=stall_timeout)
                    try:os.killpg(proc.pid,signal.SIGTERM) if os.name == "posix" else proc.terminate()
                    except ProcessLookupError:pass
                    try:await asyncio.wait_for(proc.wait(),2)
                    except asyncio.TimeoutError:
                        try:os.killpg(proc.pid,signal.SIGKILL) if os.name == "posix" else proc.kill()
                        except ProcessLookupError:pass
                        await proc.wait()
                    break
                if not raw: break
                # A read can end in the middle of a line or UTF-8 character.
                # Emit only complete lines; carry the unfinished tail to the
                # next read so module names and structured diagnostics survive.
                text=line_buffer+decoder.decode(raw)
                # CR-only progress lines are complete too; CRLF just leaves an
                # empty part, which the normal blank-line filter ignores.
                parts=text.replace("\r", "\n").split("\n")
                line_buffer=parts.pop()
                for part in parts:
                    line=part.strip()
                    if not line:continue
                    if artifact_dir is None:
                        session_match=re.search(r"(?:^|\s)session:\s*([A-Za-z0-9._-]+)",line)
                        if session_match:
                            from vmodel_contracts.runtime import artifacts_root
                            artifact_dir=artifacts_root((env or {}).get("VMODEL_ARTIFACTS_DIR") or os.environ.get("VMODEL_ARTIFACTS_DIR"))/session_match.group(1)/"interview"
                            usage_path=artifact_dir/"usage.jsonl";llm_path=artifact_dir/"llm-calls.jsonl"
                            usage_start=line_count(usage_path);llm_start=line_count(llm_path)
                            self.pricing_paths[task_id]=(usage_path,llm_path);self.pricing_usage_cursor[task_id]=usage_start;self.pricing_llm_cursor[task_id]=llm_start;self.pricing_wall_cursor[task_id]=time.perf_counter()
                    # Subprocesses are non-interactive executors. Interview control moved to
                    # the typed HTTP service; stdout/stderr are diagnostics only.
                    from .progress_adapter import ingest_generator_line
                    structured=ingest_generator_line(self.store,task_id,line,launched_phase,(env or {}).get("VMODEL_PROGRESS_SESSION_ID"))
                    if not structured:
                        activity("engine.output","Engine diagnostic output",task_id=task_id,phase=phase,stream="combined",line=line)
                    if structured:
                        structured_progress_seen=True
                        continue
                    if line.startswith("Error:") and cli_error is None:cli_error=line
                    if line.startswith("Traceback (most recent call last):"):traceback_started=True
                    missing=_MISSING_MODULE.search(line)
                    if missing:missing_module=missing.group(1);traceback_started=True
                    if traceback_started: continue
                    if infer_phase: phase = self._phase(line, phase)
                    # Legacy console parsing is a migration fallback only.
                    if structured_progress_seen:continue
                    match = _PROGRESS.search(line)
                    completed = int(match.group(3)) if match and match.group(3) else 0
                    total = int(match.group(4)) if match and match.group(4) else None
                    self.store.update_phase(task_id,phase,"running",detail=line,completed=completed,total=total)
                    payload={"completed":completed,"total":total}
                    self.store.add_event(task_id,"progress",line,phase_key=phase,payload=payload)
            # The final diagnostic may not end with a newline. Log it as one
            # intact line instead of dropping it or splitting it by read size.
            final_line=(line_buffer+decoder.decode(b"",final=True)).strip()
            if final_line:
                activity("engine.output","Engine diagnostic output",task_id=task_id,phase=phase,stream="combined",line=final_line)
                missing=_MISSING_MODULE.search(final_line)
                if missing:missing_module=missing.group(1)
                if final_line.startswith("Error:") and cli_error is None:cli_error=final_line
            code = await proc.wait()
            if code==0 and artifact_dir:
                from .pricing import price_execution
                final_usage_start=self.pricing_usage_cursor.get(task_id,usage_start);final_llm_start=self.pricing_llm_cursor.get(task_id,llm_start)
                final_wall_ms=max(0,(time.perf_counter()-self.pricing_wall_cursor.get(task_id,run_started))*1000) if task_id in self.pricing_totals else max(0,(time.perf_counter()-run_started-self.pricing_wait_seconds.get(task_id,0))*1000)
                for quote in price_execution(usage_path,llm_path,final_wall_ms,final_usage_start,final_llm_start):
                    priced_phase=quote.pop("phase")
                    self.store.add_event(task_id,"price",f"Price {quote['price']:,.2f} units",phase_key=priced_phase,payload=quote)
                    self.store.audit("pricing","phase.priced",task_id,{"phase":priced_phase,**quote})
            if task_id in self.interrupted_for_shutdown:
                # Keep the durable task phase running: startup will recover it
                # from the latest interview session checkpoint.
                self.store.add_event(task_id,"interrupted","Server restart interrupted the engine; it will resume from its checkpoint",phase_key=phase)
            elif code == 0:
                # Executables can own founder decisions that approve artifacts.
                # Callers declare those boundaries; ProcessRunner stays generic.
                for completed_phase in completion_phases:
                    self.store.update_phase(task_id, completed_phase, "approved", detail="Founder approved in the engine")
                current=self.store.task(task_id)
                phase_row=next(p for p in current["phases"] if p["phase_key"]==phase)
                from .phases import PHASE_BY_KEY
                if phase_row["state"] != "approved" and PHASE_BY_KEY[launched_phase].executor=="generator":
                    # Exit 0 is not a generated artifact. Never park the user
                    # at a gate with nothing to review.
                    from vmodel_contracts.runtime import artifacts_root
                    source=artifact_dir or (artifacts_root((env or {}).get("VMODEL_ARTIFACTS_DIR") or os.environ.get("VMODEL_ARTIFACTS_DIR")) / (env or {}).get("VMODEL_PROGRESS_SESSION_ID", "") / "spec")
                    expected=PHASE_BY_KEY[phase].artifacts
                    available=[source/name for name in expected if (source/name).is_file()]
                    if available:
                        from pydantic import TypeAdapter
                        from vmodel_contracts.verification import AcceptanceTest
                        try:
                            if phase=="tests":TypeAdapter(list[AcceptanceTest]).validate_json(available[0].read_text())
                            elif available[0].suffix==".json":json.loads(available[0].read_text())
                        except (ValueError,OSError) as exc:
                            error=f"Error: {phase} exited successfully but its artifact is unreadable or invalid ({type(exc).__name__}: {str(exc)[:240]}). No approval gate was opened."
                            self.store.update_phase(task_id,phase,"failed",detail=error)
                            self.store.add_event(task_id,"error",error,phase_key=phase,payload={"artifact":str(available[0]),"exit_code":0})
                            return 1
                    if not available:
                        error=f"Error: {phase} exited successfully but no configured artifact was written ({', '.join(expected)}). Check the generator checkpoint and run.log; no approval gate was opened."
                        self.store.update_phase(task_id,phase,"failed",detail=error)
                        self.store.add_event(task_id,"error",error,phase_key=phase,payload={"missing_artifacts":list(expected),"source_dir":str(source),"exit_code":0})
                        return 1
                    self.store.update_phase(task_id, phase, "awaiting_approval", detail="Generated; waiting for founder approval")
                    gate=self.store.wait_at_gate(task_id,phase)
                    self.store.add_event(task_id,"gate","Generated artifact is waiting for founder approval",phase_key=phase,payload={"gate_key":gate["gate_key"] if gate else None})
                    # Explicit open-gate policy approves in wait_at_gate. It
                    # must use the same continuation path as a founder click.
                    if gate is None and next(p for p in self.store.task(task_id)["phases"] if p["phase_key"]==phase)["state"] == "approved":
                        continuation_phase = phase
                self.store.add_event(task_id, "complete", "Engine process completed", phase_key=phase)
                if completion_phases and self.on_complete is not None:
                    continuation_phase = completion_phases[-1]
            else:
                if stalled:
                    error=(f"Error: the engine produced no output for {stall_timeout:g} seconds, so the run was stopped. Likely cause: "
                           f"the process blocked silently - waiting on input that will never arrive, or a hung call with no heartbeat. "
                           f"Next: check run.log for the last reported stage, fix the cause, then retry.")
                    cause="The engine went silent and was stopped as stalled."
                    next_step="Check run.log for the last reported stage, fix the cause, then retry."
                elif missing_module:
                    error=f"Error: {phase} subprocess dependency '{missing_module}' is missing. Likely cause: the selected Python environment is incomplete. Next: install the {phase} executor and its dependencies into the selected environment, restart Requi.re, and retry. Exit code: {code}."
                    cause=f"Python could not import {missing_module}."
                    next_step=f"Install the {phase} executor dependencies in the selected Python environment, then retry. The raw traceback is in run.log."
                else:
                    cause=cli_error or f"The {phase} CLI exited before completing."
                    next_step="Check run.log for the full diagnostic, fix the named cause, then retry."
                    error=f"Error: {phase} subprocess stopped with exit code {code}. Likely cause: {cause} Next: {next_step}"
                self.store.update_phase(task_id,phase,"failed",detail=error)
                self.store.add_event(task_id,"error",error,phase_key=phase,payload={"exit_code":code,"module":missing_module,"cause":cause,"next":next_step})
            return code
        except Exception as exc:
            # Parser/protocol failures used to escape without closing the child or
            # changing durable state, leaving a task marked running with no process.
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid,signal.SIGTERM) if os.name == "posix" else proc.terminate()
                except ProcessLookupError:pass
                try:await asyncio.wait_for(proc.wait(),1)
                except asyncio.TimeoutError:
                    try:os.killpg(proc.pid,signal.SIGKILL) if os.name == "posix" else proc.kill()
                    except ProcessLookupError:pass
                    await proc.wait()
            error=f"Error: engine stream handling stopped. Likely cause: {type(exc).__name__}: {exc}. Next: restart Requi.re to resume the saved interview checkpoint; rerun Start only if recovery cannot resume it."
            self.store.update_phase(task_id,phase,"failed",detail=error)
            self.store.add_event(task_id,"error",error,phase_key=phase,payload={"cause":f"{type(exc).__name__}: {exc}","next":"Restart Requi.re to resume the saved interview checkpoint; rerun Start only if recovery cannot resume it."})
            activity("engine.stream.failure","Engine stream handling failed visibly",task_id=task_id,phase=phase,error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.processes.pop(task_id, None);self.waiting.discard(task_id);self.pending_decisions.pop(task_id,None);self.active_work.pop(task_id,None);self.interrupted_for_shutdown.discard(task_id);self.pricing_wait_started.pop(task_id,None);self.pricing_wait_seconds.pop(task_id,None);self.pricing_usage_cursor.pop(task_id,None);self.pricing_llm_cursor.pop(task_id,None);self.pricing_wall_cursor.pop(task_id,None);self.pricing_totals.pop(task_id,None);self.pricing_paths.pop(task_id,None)
            # The finished process must release its task slot before the next
            # executable starts. This is the shared process boundary.
            if continuation_phase is not None and self.on_complete is not None:
                try:
                    await self.on_complete(task_id, continuation_phase)
                except Exception as exc:
                    error=(f"Error: the next V-model phase did not start. Likely cause: "
                           f"{type(exc).__name__}: {exc}. Next: restart Requi.re; startup recovery will retry the durable handoff.")
                    self.store.add_event(task_id,"error",error,phase_key=continuation_phase,
                                         payload={"cause":f"{type(exc).__name__}: {exc}","next":"Restart Requi.re; startup recovery will retry the durable handoff.","completed_phase":continuation_phase})
                    activity("orchestrator.failure","Next phase launch failed",task_id=task_id,phase=continuation_phase,error=f"{type(exc).__name__}: {exc}")
                    raise

    async def shutdown(self, grace_seconds: float = 3.0) -> None:
        active=list(self.processes.items())
        for task_id,proc in active:
            if proc.returncode is not None:continue
            self.interrupted_for_shutdown.add(task_id)
            activity("engine.shutdown.signal","Stopping engine for server shutdown",task_id=task_id,pid=proc.pid,signal="SIGTERM")
            try:
                os.killpg(proc.pid,signal.SIGTERM) if os.name == "posix" else proc.terminate()
            except ProcessLookupError:pass
        if active:
            try:await asyncio.wait_for(asyncio.gather(*(p.wait() for _,p in active)),grace_seconds)
            except asyncio.TimeoutError:
                for task_id,proc in active:
                    if proc.returncode is not None:continue
                    activity("engine.shutdown.kill","Engine ignored shutdown grace period",task_id=task_id,pid=proc.pid,signal="SIGKILL")
                    try:os.killpg(proc.pid,signal.SIGKILL) if os.name == "posix" else proc.kill()
                    except ProcessLookupError:pass
                await asyncio.gather(*(p.wait() for _,p in active),return_exceptions=True)
        activity("engine.shutdown.complete","Engine shutdown complete",processes=len(active))

    async def request_decision(self, task_id: str, phase: str, decision: dict) -> str:
        if task_id in self.decisions: raise RuntimeError("task already has a pending decision")
        future=asyncio.get_running_loop().create_future();self.decisions[task_id]=future;self.pending_decisions[task_id]=decision;self.waiting.add(task_id)
        self.active_work[task_id]=ActiveWork("decision",phase,"waiting",time.time(),decision,str(decision.get("decision_id") or ""))
        self.store.add_event(task_id,"founder_decision",decision["prompt"],phase_key=phase,payload={"waiting_input":True,"question":decision["prompt"],"decision":decision})
        try:return await future
        finally:self.decisions.pop(task_id,None);self.pending_decisions.pop(task_id,None);self.waiting.discard(task_id);self.active_work.pop(task_id,None)

    def pause_for_requirement(self, task_id: str, phase: str, requirement: dict) -> None:
        self.active_work[task_id]=ActiveWork("requirement",phase,"waiting",time.time(),durable_ref=requirement.get("path"),requirement=requirement)

    def work_state(self, task_id: str) -> ActiveWork | None:
        return self.active_work.get(task_id)

    def input_state(self, task_id: str) -> dict:
        work=self.work_state(task_id)
        return work.input_state() if work else {"waiting":False,"process_live":False,"decision":None,"requirement":None}

    def _emit_incremental_price(self,task_id:str)->None:
        paths=self.pricing_paths.get(task_id)
        if not paths:return
        from .pricing import line_count,price_execution
        usage_path,llm_path=paths;now=time.perf_counter();prior=self.pricing_totals.get(task_id,{})
        quotes=price_execution(usage_path,llm_path,max(0,(now-self.pricing_wall_cursor.get(task_id,now))*1000),self.pricing_usage_cursor.get(task_id,0),self.pricing_llm_cursor.get(task_id,0))
        for quote in quotes:
            phase=quote.pop("phase");total=prior.setdefault(phase,{"input_tokens":0,"output_tokens":0,"tokens":0,"deterministic_ms":0.0,"token_price":0.0,"infra_price":0.0,"price":0.0})
            for key in total:total[key]=round(total[key]+quote[key],3)
            payload={**total,"rates":quote["rates"],"incremental":True}
            self.store.add_event(task_id,"price",f"Price {payload['price']:,.2f} units",phase_key=phase,payload=payload)
        self.pricing_usage_cursor[task_id]=line_count(usage_path);self.pricing_llm_cursor[task_id]=line_count(llm_path);self.pricing_wall_cursor[task_id]=now

    async def send_input(self, task_id: str, text: str, decision_id: str | None = None) -> None:
        pending=self.pending_decisions.get(task_id)
        if decision_id is not None and (not pending or pending.get("decision_id") != decision_id):
            raise RuntimeError("submitted decision does not match the live typed decision")
        if task_id in self.decisions:
            self.pending_decisions.pop(task_id,None);self.waiting.discard(task_id)
            self.decisions[task_id].set_result(text);return
        proc = self.processes.get(task_id)
        if not proc or not proc.stdin: raise RuntimeError("task is not waiting for input")
        proc.stdin.write((text + "\n").encode()); await proc.stdin.drain()
        wait_started=self.pricing_wait_started.pop(task_id,None)
        if wait_started is not None:
            self.pricing_wait_seconds[task_id]=self.pricing_wait_seconds.get(task_id,0)+(time.perf_counter()-wait_started)
            self.pricing_wall_cursor[task_id]=self.pricing_wall_cursor.get(task_id,time.perf_counter())+(time.perf_counter()-wait_started)
        self._emit_incremental_price(task_id)
        self.pending_decisions.pop(task_id,None)
        current=self.active_work.get(task_id)
        if current:self.active_work[task_id]=ActiveWork(current.owner,current.phase,"running",current.started_at,None,current.durable_ref)
        if task_id in self.waiting:
            self.waiting.discard(task_id);activity("engine.input.resume","Answer received; engine resumed",task_id=task_id,phase=self.store.task(task_id).get("active_phase"))
