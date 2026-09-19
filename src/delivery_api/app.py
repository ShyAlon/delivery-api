"""Thin job-control API plus the V-model web console, served on one port."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vmodel_contracts import RunState, RunStatus

from . import config
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from . import auth
from .jobs import JobQueue
from .runner import ProcessRunner
from .store import create_store
from .simulation import Simulation, ImportValidationError
from . import bundles

WEB_DIR = Path(__file__).parent / "web"
app = FastAPI(title="V-model console", version="0.2.0")
store = create_store(config.DATABASE_URL)
jobs = JobQueue(store)
runner = ProcessRunner(store)
simulation = Simulation(store)
app.state.store = store
log = logging.getLogger("delivery_api.http")
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

@app.middleware("http")
async def request_log(request, call_next):
    try:
        auth.csrf_guard(request)
    except HTTPException as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request failed method=%s path=%s", request.method, request.url.path)
        raise
    who = auth.actor(request).label
    log.info("request method=%s path=%s status=%s duration_ms=%d actor=%s",
             request.method, request.url.path, response.status_code,
             (time.monotonic() - started) * 1000, who)
    return response

if config.ADMIN_TOKEN == "dev-admin-token":
    import logging
    logging.getLogger("delivery_api").warning(
        "DELIVERY_ADMIN_TOKEN is unset - using the local development default. "
        "Set DELIVERY_ADMIN_TOKEN before exposing this service."
    )

@app.on_event("startup")
async def _start():
    config.validate_auth_config()
    await jobs.start()

@app.on_event("shutdown")
async def _stop(): await jobs.stop()

@app.get("/", include_in_schema=False)
def index(): return FileResponse(WEB_DIR / "index.html")

@app.get("/api/auth")
def auth_status(request: Request):
    a = auth.actor(request)
    return {"mode": "oauth" if auth.enabled() else "local", "authenticated": a.id not in {"anonymous"},
            "actor": a.label, "admin": a.admin, "providers": [p for p, ident in (("google",config.GOOGLE_CLIENT_ID),("github",config.GITHUB_CLIENT_ID)) if ident]}

@app.get("/auth/{provider}/start")
def oauth_start(request: Request, provider: str):
    try: return auth.oauth_start(request, provider)
    except ValueError as e: raise HTTPException(404, str(e))

@app.get("/auth/{provider}/callback", name="oauth_callback")
def oauth_callback(request: Request, provider: str, code: str, state: str):
    try: return auth.oauth_callback(request, provider, code, state)
    except (ValueError, KeyError) as e: raise HTTPException(401, "OAuth profile was incomplete or invalid.") from e

@app.post("/auth/logout")
def logout(request: Request):
    sid=request.cookies.get(config.SESSION_COOKIE)
    if sid: store.delete_session(sid)
    from fastapi.responses import JSONResponse
    response=JSONResponse({"status":"signed_out"}); response.delete_cookie(config.SESSION_COOKIE,path="/"); return response

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse(WEB_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/tasks/{task_id}/view", include_in_schema=False)
def task_view(task_id: str): return FileResponse(WEB_DIR / "index.html")

@app.get("/sessions", include_in_schema=False)
def sessions_view(): return FileResponse(WEB_DIR / "index.html")

@app.get("/sessions/{session_id}/view", include_in_schema=False)
def session_view(session_id: str): return FileResponse(WEB_DIR / "index.html")

@app.get("/api/sessions")
def sessions(): return {"sessions":bundles.discover(),"artifacts_root":str(bundles.root())}

class ArtifactsRootRequest(BaseModel): path: str
@app.post("/api/settings/artifacts-root")
def set_artifacts_root(req: ArtifactsRootRequest, a: auth.Actor = Depends(auth.require_actor)):
    try:return bundles.save_root(req.path)
    except ValueError as e:raise HTTPException(400,{"error":"Artifacts folder could not be loaded.","cause":str(e),"next":"Choose the folder that directly contains your session directories, then validate again."})

@app.get("/api/sessions/{session_id}")
def session_bundle(session_id: str):
    try:return bundles.bundle(session_id)
    except (FileNotFoundError,ValueError):raise HTTPException(404,"session bundle not found")


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
def start_run(req: StartRequest, request: Request, a: auth.Actor = Depends(auth.require_actor)):
    link = store.link(req.token)
    if not link: raise HTTPException(403, "unknown or spent link")
    run = store.run(link["run_id"])
    if run["state"] != RunState.CREATED.value: raise HTTPException(409, f"run already {run['state']}")
    allowed, quota = (True, {"user_id":"local","token_quota":0,"tokens_used":0}) if not auth.enabled() else store.quota_allows(a.id)
    store.audit(a.label, "quota.checked", detail={**quota, "allowed": allowed, "run_id": link["run_id"]})
    if not allowed: raise HTTPException(402, {"error":"Token budget exhausted.","cause":f"This user has used {quota['tokens_used']} of {quota['token_quota']} tokens.","next":"Ask the administrator to raise the user token quota before starting another run."})
    store.attribute_run(link["run_id"], a.id)
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
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

@app.post("/runs/{run_id}/usage")
def add_usage(run_id: str, rec: UsageRecord, request: Request):
    run = store.run(run_id)
    if not run: raise HTTPException(404)
    a=auth.actor(request, required=auth.enabled())
    owner=run.get("owner_id") or a.id
    input_tokens,output_tokens=rec.input_tokens,rec.output_tokens
    if not input_tokens and not output_tokens:
        try:
            raw=json.loads(rec.record_json); usage=raw.get("usage",raw)
            input_tokens=int(usage.get("input_tokens",usage.get("prompt_tokens",0)) or 0)
            output_tokens=int(usage.get("output_tokens",usage.get("completion_tokens",0)) or 0)
        except (ValueError,TypeError,json.JSONDecodeError): pass
    if auth.enabled() and owner != a.id and not a.admin:
        raise HTTPException(403,"Usage may only be attributed by the run owner or administrator.")
    if auth.enabled():
        allowed,q=store.quota_allows(owner)
        projected=q["tokens_used"]+input_tokens+output_tokens
        if not allowed or projected > q["token_quota"]:
            store.audit(a.label,"quota.usage_rejected",detail={**q,"projected_tokens":projected,"run_id":run_id})
            store.set_state(run_id,RunState.BUDGET_STOPPED)
            raise HTTPException(402,{"error":"Token budget exhausted.","cause":f"Recording this call would use {projected} of {q['token_quota']} tokens.","next":"Stop model calls and ask the administrator to raise the user token quota."})
    total = store.add_user_usage(run_id, owner, rec.record_json, rec.cost_usd, input_tokens, output_tokens)
    if auth.enabled(): store.audit(a.label,"quota.usage_accepted",detail={"user_id":owner,"input_tokens":input_tokens,"output_tokens":output_tokens,"run_id":run_id})
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
def create_task(req: TaskCreate, request: Request, a: auth.Actor = Depends(auth.require_actor)):
    try: task=store.create_task(req.title, req.brief)
    except ValueError as e: raise HTTPException(409,{"error":str(e),"cause":"Task names must be unique (ignoring capitalization).","next":"Choose a different task name or rename the existing task."})
    store.audit(a.label, "task.created_by", task["task_id"])
    return task

class TaskRename(BaseModel):
    title: str = Field(min_length=1,max_length=120)

@app.patch("/api/tasks/{task_id}")
def rename_task(task_id:str,req:TaskRename,a:auth.Actor=Depends(auth.require_actor)):
    try:return store.rename_task(task_id,req.title,a.label)
    except KeyError:raise HTTPException(404,{"error":"Task not found.","cause":"It may already have been deleted.","next":"Return to the task list."})
    except ValueError as e:raise HTTPException(409,{"error":str(e),"cause":"Task names must be unique (ignoring capitalization).","next":"Choose a different task name."})

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    try:store.delete_task(task_id,a.label)
    except KeyError:raise HTTPException(404,{"error":"Task not found.","cause":"It may already have been deleted.","next":"Return to the task list."})
    except ValueError as e:raise HTTPException(409,{"error":str(e),"cause":"Active work must stop before its data is removed.","next":"Wait for the replay to finish, then delete the task."})
    return {"status":"deleted","task_id":task_id,"source_artifacts_deleted":False}

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
def rerun(task_id: str, req: RerunRequest, request: Request, a: auth.Actor = Depends(auth.require_actor)):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    if not req.confirm:
        downstream = [p["phase_key"] for p in store.task(task_id)["phases"] if p["position"] >= next(p["position"] for p in store.task(task_id)["phases"] if p["phase_key"] == req.from_phase)]
        return {"status": "preview", "from_phase": req.from_phase, "policy": "archive", "downstream": downstream,
                "message": "Downstream artifacts will be marked stale and retained under their generation. Their gates will require re-approval."}
    try: generation = store.prepare_rerun(task_id, req.from_phase, "archive", a.label)
    except (ValueError, KeyError) as e:
        raise HTTPException(409, {"error": str(e), "cause": "The selected phase is invalid.",
                                  "next": "Choose one of the phases shown on this task."})
    store.add_event(task_id, "rerun", f"Prepared generation {generation} from {req.from_phase}", phase_key=req.from_phase)
    return {"status": "prepared", "generation": generation, "policy": "archive"}

@app.get("/api/tasks/{task_id}/audit")
def task_audit(task_id: str, request: Request):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    return {"actor_mode": auth.actor(request).label, "entries": store.audit_entries(task_id)}

@app.post("/api/simulations", status_code=201)
async def import_simulation(usage_file: UploadFile = File(...), title: str | None = Form(default=None)):
    # Browsers cannot upload a directory as a path. The picker selects the
    # run's usage.jsonl; persist a private copy so replay does not depend on
    # client filesystem paths or browser-specific directory APIs.
    if Path(usage_file.filename or "").name != "usage.jsonl":
        raise HTTPException(400,{"error":"Select the run's usage.jsonl file.","cause":"The chosen file is not named usage.jsonl.","next":"Open the recorded run folder and choose usage.jsonl."})
    import tempfile
    root=Path(tempfile.mkdtemp(prefix="vmodel-import-"));dest=root/"usage.jsonl"
    total=0
    with dest.open("wb") as fh:
        while chunk:=await usage_file.read(1024*1024):
            total+=len(chunk)
            if total>20_000_000: raise HTTPException(413,{"error":"The selected file is too large.","cause":"The 20 MB upload safety limit was exceeded.","next":"Choose the original usage.jsonl or archive the oversized recording."})
            fh.write(chunk)
    try:return simulation.import_run(root,title)
    except ImportValidationError as e:raise HTTPException(400,{"error":str(e),"cause":e.cause,"next":e.next_action})

class ReplayRequest(BaseModel):
    speed: float | None = 100

@app.post("/api/tasks/{task_id}/simulate")
async def replay_simulation(task_id: str, req: ReplayRequest):
    try: simulation.start(task_id, None if req.speed in (None, 0) else req.speed)
    except ValueError as e: raise HTTPException(409, {"error": str(e), "cause": "The task cannot start this replay.", "next": "Import a recorded run or wait for the current replay."})
    except Exception as e:
        raise HTTPException(500, {"error": "The recorded replay could not start.", "cause": f"{type(e).__name__}: {str(e)[:300]}", "next": "Retry once. If it repeats, restart scripts/run-ui.sh and open this task again."})
    store.audit("local user", "simulation.started", task_id, {"speed": "instant" if req.speed in (None,0) else req.speed})
    return {"status":"started","simulated":True,"speed":"instant" if req.speed in (None,0) else req.speed}

class GateToggle(BaseModel): is_open: bool
@app.post("/api/tasks/{task_id}/gates/{gate_key}/toggle")
def toggle_gate(task_id:str,gate_key:str,req:GateToggle,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    try:return store.set_gate_open(task_id,gate_key,req.is_open,a.label)
    except KeyError:raise HTTPException(404,"gate not found")

class GateDecision(BaseModel):
    action: Literal["approve","edit","redo"]
    selected_option: str|None=None
    edited_text: str|None=None
    steer: str|None=None
@app.post("/api/tasks/{task_id}/gates/{gate_key}/decide")
def decide_gate(task_id:str,gate_key:str,req:GateDecision,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    try:return store.decide_gate(task_id,gate_key,req.action,selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,actor=a.label)
    except KeyError:raise HTTPException(404,"gate not found")
    except ValueError as e:raise HTTPException(400,{"error":str(e),"cause":"The gate decision is incomplete.","next":"Choose, edit, or explain what the redo should change."})

class QuotaUpdate(BaseModel): token_quota: int = Field(ge=0)
@app.get("/api/admin/users/{user_id}/quota")
def get_quota(user_id:str, request:Request, a:auth.Actor=Depends(auth.require_admin)):
    q=store.user_quota(user_id)
    if not q: raise HTTPException(404,"user not found")
    return q
@app.put("/api/admin/users/{user_id}/quota")
def set_quota(user_id:str, req:QuotaUpdate, request:Request, a:auth.Actor=Depends(auth.require_admin)):
    try:return store.set_user_quota(user_id,req.token_quota,a.label)
    except KeyError:raise HTTPException(404,"user not found")

@app.get("/health")
def health(): return {"ok": True, "total_spend_usd": store.total_spend(), "tasks": len(store.list_tasks())}
