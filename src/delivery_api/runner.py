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
    "interview": "intent", "distill": "intent", "intent": "intent", "conops": "conops",
    "needs": "needs", "srs": "srs", "requirement": "srs", "architecture": "architecture",
    "item": "specs", "spec": "specs", "test": "tests", "verification": "tests",
}

@dataclass
class ProcessRunner:
    store: Storage
    processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)

    def _phase(self, text: str, fallback: str) -> str:
        lower = text.lower()
        return next((phase for word, phase in _PHASE_HINTS.items() if word in lower), fallback)

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
        last_line = time.monotonic()
        traceback_started = False
        missing_module: str | None = None

        async def heartbeat():
            while proc.returncode is None:
                await asyncio.sleep(15)
                self.store.add_event(task_id, "heartbeat", "Engine is still working", phase_key=phase,
                                     payload={"silent_seconds": round(time.monotonic() - last_line)})

        beat = asyncio.create_task(heartbeat())
        try:
            assert proc.stdout
            # Read chunks, not lines: input() prompts normally have no trailing newline.
            # Chunk reads expose the question immediately instead of making the browser
            # look frozen while the engine waits for stdin.
            while True:
                raw = await proc.stdout.read(512)
                if not raw: break
                for line in filter(None,(part.strip() for part in raw.decode(errors="replace").splitlines())):
                    last_line = time.monotonic()
                    waiting_input = line.endswith(("You:", "[y/N]", "[y/N] ")) or "Describe your product idea:" in line or "Approve this intent model?" in line
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
                    if waiting_input:payload["waiting_input"]=True
                    self.store.add_event(task_id,"waiting_input" if waiting_input else "progress",line,phase_key=phase,payload=payload)
            code = await proc.wait()
            if code == 0:
                self.store.update_phase(task_id, phase, "done", detail="Engine stage complete")
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
            beat.cancel(); self.processes.pop(task_id, None)

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
