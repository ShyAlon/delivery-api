"""Thin job-control API plus the V-model web console, served on one port."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vmodel_contracts import RunState, RunStatus

from . import config
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from .jobs import JobQueue
from .runner import ProcessRunner
from .store import create_store

WEB_DIR = Path(__file__).parent / "web"
app = FastAPI(title="V-model console", version="0.2.0")
store = create_store(config.DATABASE_URL)
jobs = JobQueue(store)
runner = ProcessRunner(store)
log = logging.getLogger("delivery_api.http")
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

@app.middleware("http")
async def request_log(request, call_next):
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request failed method=%s path=%s", request.method, request.url.path)
        raise
    log.info("request method=%s path=%s status=%s duration_ms=%d actor=local_user",
             request.method, request.url.path, response.status_code,
             (time.monotonic() - started) * 1000)
    return response

if config.ADMIN_TOKEN == "dev-admin-token":
    import logging
    logging.getLogger("delivery_api").warning(
        "DELIVERY_ADMIN_TOKEN is unset - using the local development default. "
        "Set DELIVERY_ADMIN_TOKEN before exposing this service."
    )

@app.on_event("startup")
async def _start(): await jobs.start()

@app.on_event("shutdown")
async def _stop(): await jobs.stop()

@app.get("/", include_in_schema=False)
def index(): return FileResponse(WEB_DIR / "index.html")

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse(WEB_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/tasks/{task_id}/view", include_in_schema=False)
def task_view(task_id: str): return FileResponse(WEB_DIR / "index.html")


def _admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != config.ADMIN_TOKEN: raise HTTPException(403, "link creation is admin-only")

class LinkRequest(BaseModel): founder_label: str
class StartRequest(BaseModel):
    token: str
    free_text: str
    known_answers: dict[str, str] = Field(default_factory=dict)

@app.post("/links", dependencies=[Depends(_admin)])
def create_link(req: LinkRequest):
    if not check_global_budget(store.total_spend(), config.GLOBAL_BUDGET_USD):
        raise HTTPException(402, "global spend cap reached - raise DELIVERY_GLOBAL_BUDGET_USD if expected")
    link = store.create_link(req.founder_label)
    return {"url": f"/runs/start?token={link['token']}", **link}

@app.post("/runs")
def start_run(req: StartRequest):
    link = store.link(req.token)
    if not link: raise HTTPException(403, "unknown or spent link")
    run = store.run(link["run_id"])
    if run["state"] != RunState.CREATED.value: raise HTTPException(409, f"run already {run['state']}")
    store.set_state(link["run_id"], RunState.IN_INTERVIEW)
    store.set_submission(link["run_id"], req.model_dump_json())
    return {"run_id": link["run_id"], "state": RunState.IN_INTERVIEW.value}

@app.get("/runs/{run_id}", response_model=RunStatus)
def run_status(run_id: str):
    run = store.run(run_id)
    if not run: raise HTTPException(404)
    return RunStatus(run_id=run_id, state=RunState(run["state"]), token_budget_usd=run["budget_usd"])

class UsageRecord(BaseModel):
    record_json: str
    cost_usd: float

@app.post("/runs/{run_id}/usage")
def add_usage(run_id: str, rec: UsageRecord):
    run = store.run(run_id)
    if not run: raise HTTPException(404)
    total = store.add_usage(run_id, rec.record_json, rec.cost_usd)
    try: check_run_budget(total, run["budget_usd"], run_id)
    except BudgetExceeded:
        store.set_state(run_id, RunState.BUDGET_STOPPED)
        raise HTTPException(402, f"run stopped by budget guard at ${total:.2f}; raise DELIVERY_PER_RUN_BUDGET_USD and retry")
    return {"run_total_usd": total}

@app.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str): return store.artifacts(run_id)

# Task-oriented UI API.
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=4000)

@app.get("/api/tasks")
def list_tasks(): return {"tasks": store.list_tasks()}

@app.post("/api/tasks", status_code=201)
def create_task(req: TaskCreate): return store.create_task(req.title, req.brief)

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = store.task(task_id)
    if not task: raise HTTPException(404, "task not found")
    return {**task, "events": store.events(task_id), "artifacts": store.task_artifacts(task_id)}

@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(default=0, ge=0)):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    return {"events": store.events(task_id, after)}

@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    async def generate():
        existing = store.events(task_id)
        cursor = existing[-1]["id"] if existing else 0
        while True:
            events = store.events(task_id, cursor)
            for event in events:
                cursor = event["id"]
                yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event)}\n\n"
            yield f"event: heartbeat\ndata: {json.dumps({'task_id': task_id})}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

class RerunRequest(BaseModel):
    from_phase: str
    confirm: bool = False

@app.post("/api/tasks/{task_id}/rerun")
def rerun(task_id: str, req: RerunRequest):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    if not req.confirm:
        downstream = [p["phase_key"] for p in store.task(task_id)["phases"] if p["position"] >= next(p["position"] for p in store.task(task_id)["phases"] if p["phase_key"] == req.from_phase)]
        return {"status": "preview", "from_phase": req.from_phase, "policy": "archive", "downstream": downstream,
                "message": "Downstream artifacts will be marked stale and retained under their generation. Their gates will require re-approval."}
    try: generation = store.prepare_rerun(task_id, req.from_phase, "archive", "local user")
    except (ValueError, KeyError) as e:
        raise HTTPException(409, {"error": str(e), "cause": "The selected phase is invalid.",
                                  "next": "Choose one of the phases shown on this task."})
    store.add_event(task_id, "rerun", f"Prepared generation {generation} from {req.from_phase}", phase_key=req.from_phase)
    return {"status": "prepared", "generation": generation, "policy": "archive"}

@app.get("/api/tasks/{task_id}/audit")
def task_audit(task_id: str):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    return {"actor_mode": "local user", "entries": store.audit_entries(task_id)}

@app.get("/health")
def health(): return {"ok": True, "total_spend_usd": store.total_spend(), "tasks": len(store.list_tasks())}
