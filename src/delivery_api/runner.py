"""Subprocess job control for the existing CLIs; no pipeline logic lives here."""
from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from .store import PHASES, Storage
from .observability import event as activity

_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named [\'\"]([^\'\"]+)[\'\"]")
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

@dataclass
class ProcessRunner:
    store: Storage
    processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    waiting: set[str] = field(default_factory=set)

    def _phase(self, text: str, fallback: str) -> str:
        lower = text.lower()
        return next((phase for word, phase in sorted(_PHASE_HINTS.items(), key=lambda item: -len(item[0])) if word in lower), fallback)

    async def run(self, task_id: str, command: list[str], *, cwd: Path, phase: str = "intent",
                  env: dict[str, str] | None = None) -> int:
        if task_id in self.processes:
            raise RuntimeError("task already has a live process")
        self.store.update_phase(task_id, phase, "running", detail="Starting engine")
        self.store.add_event(task_id, "command", "Engine process started", phase_key=phase,
                             payload={"argv": command, "cwd": str(cwd)})
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env={**os.environ, **(env or {})},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        self.processes[task_id] = proc
        traceback_started = False
        missing_module: str | None = None
        prompt_lines: list[str] = []

        # The child pipeline owns progress and LLM heartbeats. Its lines name
        # the in-flight stage and elapsed time. A second generic heartbeat here
        # used to overwrite that useful state with "Engine is still working".
        try:
            assert proc.stdout
            # Read chunks, not lines: input() prompts normally have no trailing newline.
            # Chunk reads expose the question immediately instead of making the browser
            # look frozen while the engine waits for stdin.
            while True:
                raw = await proc.stdout.read(512)
                if not raw: break
                for line in filter(None,(part.strip() for part in raw.decode(errors="replace").splitlines())):
                    waiting_input = line.endswith(("You:", "[y/N]", "[y/N] ")) or "Describe your product idea:" in line or "Approve this intent model?" in line
                    if waiting_input:
                        prompt_lines.append(line)
                        if task_id not in self.waiting:
                            self.waiting.add(task_id)
                            activity("engine.waiting_input","Engine is waiting for an answer",task_id=task_id,question=" ".join(prompt_lines)[:240])
                    elif line and not _PROGRESS.search(line):
                        prompt_lines=[line]
                    activity("engine.output","Engine raw output",task_id=task_id,stream="combined",line=line)
                    if line.startswith("Traceback (most recent call last):"): traceback_started=True
                    missing = _MISSING_MODULE.search(line)
                    if missing: missing_module=missing.group(1);traceback_started=True
                    if traceback_started: continue
                    phase = self._phase(line, phase)
                    match = _PROGRESS.search(line)
                    completed = int(match.group(3)) if match and match.group(3) else 0
                    total = int(match.group(4)) if match and match.group(4) else None
                    self.store.update_phase(task_id, phase, "running", detail=line, completed=completed, total=total)
                    payload={"completed":completed,"total":total}
                    if waiting_input:
                        payload["waiting_input"]=True
                        payload["question"]="\n".join(prompt_lines).removesuffix("\nYou:").removesuffix("You:").strip()
                    self.store.add_event(task_id,"waiting_input" if waiting_input else "progress",line,phase_key=phase,payload=payload)
            code = await proc.wait()
            if code == 0:
                current=self.store.task(task_id)
                phase_row=next(p for p in current["phases"] if p["phase_key"]==phase)
                if phase_row["state"] != "approved":
                    self.store.update_phase(task_id, phase, "awaiting_approval", detail="Generated; waiting for founder approval")
                    gate=self.store.wait_at_gate(task_id,phase)
                    self.store.add_event(task_id,"gate","Generated artifact is waiting for founder approval",phase_key=phase,payload={"gate_key":gate["gate_key"] if gate else None})
                self.store.add_event(task_id, "complete", "Engine process completed", phase_key=phase)
            else:
                if missing_module:
                    error=f"Error: engine dependency '{missing_module}' is missing. Likely cause: the selected Python environment is incomplete. Next: install interview-engine into that environment, restart Requi.re, and retry. Exit code: {code}."
                    cause=f"Python could not import {missing_module}."
                    next_step="Install the interview-engine dependencies in the resolved Python environment, then retry. The raw traceback is in run.log."
                else:
                    error=f"Error: interview engine stopped with exit code {code}. Likely cause: the CLI reported a runtime failure. Next: check run.log for the raw diagnostic, fix the named cause, then retry."
                    cause="The CLI exited before the interview completed."
                    next_step="Check run.log for the raw diagnostic, fix the named cause, then retry."
                self.store.update_phase(task_id,phase,"failed",detail=error)
                self.store.add_event(task_id,"error",error,phase_key=phase,payload={"exit_code":code,"module":missing_module,"cause":cause,"next":next_step})
            return code
        finally:
            self.processes.pop(task_id, None);self.waiting.discard(task_id)

    async def shutdown(self, grace_seconds: float = 3.0) -> None:
        active=list(self.processes.items())
        for task_id,proc in active:
            if proc.returncode is not None:continue
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

    async def send_input(self, task_id: str, text: str) -> None:
        proc = self.processes.get(task_id)
        if not proc or not proc.stdin: raise RuntimeError("task is not waiting for input")
        proc.stdin.write((text + "\n").encode()); await proc.stdin.drain()
        if task_id in self.waiting:
            self.waiting.discard(task_id);activity("engine.input.resume","Answer received; engine resumed",task_id=task_id)
