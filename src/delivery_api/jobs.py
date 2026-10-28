"""Background job queue. Generation and verification are multi-minute
pipelines; Vercel's 300-second function cap means they were never allowed to
be request/response (ADR-0006). Retrofitting async later is the exact rework
the module boundaries exist to prevent - so it is async from line one.

The queue is an in-process asyncio seam for the demo skeleton; the Job
signature is the contract a real queue (Supabase Edge + pg_net, Upstash, ...)
must honor. Modules are invoked through their CLIs/contracts, never imported
across repo boundaries in production - the skeleton allows optional in-process
wiring for the all-in-one demo box, behind explicit flags.
"""
from __future__ import annotations
from .diagnostic_evidence import spawn

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from vmodel_contracts import RunState

from .budget import BudgetExceeded
from .errors import UserFacingError
from .store import Storage

JobFn = Callable[[str], Awaitable[str]]  # run_id -> artifact ref


@dataclass
class JobQueue:
    store: Storage
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _handlers: dict[str, JobFn] = field(default_factory=dict)
    _worker: asyncio.Task | None = None

    def register(self, kind: str, fn: JobFn) -> None:
        self._handlers[kind] = fn

    async def start(self) -> None:
        from .execution_fence import require_engine_execution
        require_engine_execution()
        self._worker = spawn(self._loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    async def submit(self, kind: str, run_id: str) -> None:
        from .execution_fence import require_engine_execution
        require_engine_execution()
        await self._queue.put((kind, run_id, time.time()))

    async def _loop(self) -> None:
        while True:
            kind, run_id, _ = await self._queue.get()
            handler = self._handlers.get(kind)
            if handler is None:
                continue
            try:
                ref = await handler(run_id)
                self.store.add_artifact(run_id, kind, ref)
            except BudgetExceeded:
                self.store.set_state(run_id, RunState.BUDGET_STOPPED)
            except UserFacingError as e:
                # module wired in-process already translated the pain; keep it human
                self.store.add_artifact(run_id, f"{kind}:error", e.render())
            except Exception as e:  # a failed job is a visible fact, not a silent drop
                from .diagnostic_evidence import bound,failure
                with bound(job_id=run_id):failure('queue.job.failed')
                self.store.add_artifact(
                    run_id, f"{kind}:error",
                    f"{type(e).__name__}: {e} - this is a bug or an unhandled environment failure, "
                    "not something the founder did wrong; check the service logs for the traceback.",
                )
            finally:
                self._queue.task_done()
