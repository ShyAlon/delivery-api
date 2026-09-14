"""Subprocess job control for the existing CLIs; no pipeline logic lives here."""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .store import PHASES, Storage

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
        )
        self.processes[task_id] = proc
        last_line = time.monotonic()

        async def heartbeat():
            while proc.returncode is None:
                await asyncio.sleep(15)
                self.store.add_event(task_id, "heartbeat", "Engine is still working", phase_key=phase,
                                     payload={"silent_seconds": round(time.monotonic() - last_line)})

        beat = asyncio.create_task(heartbeat())
        try:
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if not line: continue
                last_line = time.monotonic()
                phase = self._phase(line, phase)
                match = _PROGRESS.search(line)
                completed = int(match.group(3)) if match and match.group(3) else 0
                total = int(match.group(4)) if match and match.group(4) else None
                self.store.update_phase(task_id, phase, "running", detail=line, completed=completed, total=total)
                self.store.add_event(task_id, "progress", line, phase_key=phase,
                                     payload={"completed": completed, "total": total})
            code = await proc.wait()
            if code == 0:
                self.store.update_phase(task_id, phase, "done", detail="Engine stage complete")
                self.store.add_event(task_id, "complete", "Engine process completed", phase_key=phase)
            else:
                self.store.update_phase(task_id, phase, "failed", detail=f"Engine exited with status {code}")
                self.store.add_event(task_id, "error", f"Engine exited with status {code}", phase_key=phase,
                                     payload={"cause": "The CLI reported a failure.", "next": "Read the last progress line, fix the named cause, then resume this task."})
            return code
        finally:
            beat.cancel(); self.processes.pop(task_id, None)

    async def send_input(self, task_id: str, text: str) -> None:
        proc = self.processes.get(task_id)
        if not proc or not proc.stdin: raise RuntimeError("task is not waiting for input")
        proc.stdin.write((text + "\n").encode()); await proc.stdin.drain()
