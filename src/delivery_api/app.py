"""Thin job-control API plus the V-model web console, served on one port."""
from __future__ import annotations

import asyncio
import json
import signal
import threading
import logging
import time
import os
import sys
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from vmodel_contracts import RunState, RunStatus

from . import config
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from . import auth
from .jobs import JobQueue
from .runner import ProcessRunner
from .orchestrator import PhaseOrchestrator
from .engine_runtime import EngineRuntimeError, resolve_engine_runtime, discover_engine_runtimes, save_python, saved_python
from .store import create_store
from .simulation import Simulation, ImportValidationError
from . import bundles
from .observability import event as activity

def _runtime_version() -> dict:
    root=Path(__file__).resolve()
    for parent in root.parents:
        manifest=parent/"MANIFEST.txt"
        if manifest.is_file():
            repos={}
            in_repos=False
            for line in manifest.read_text(errors="replace").splitlines():
                if line=="Repositories:":in_repos=True;continue
                if line=="Installed packages:":break
                if in_repos and line.strip():
                    parts=line.split()
                    if len(parts)>=2:repos[parts[0]]=parts[1].split(":",1)[0]
            return {"runtime_id":repos.get("delivery-api","unknown"),"repositories":repos,"manifest":str(manifest)}
    return {"runtime_id":"development","repositories":{},"manifest":None}

RUNTIME_VERSION=_runtime_version()

WEB_DIR = Path(__file__).parent / "web"
app = FastAPI(title="Requi.re API", version="0.2.0")
store = (__import__('delivery_api.store',fromlist=['TursoReplicaStore']).TursoReplicaStore(str(config.local_database_path()),config.TURSO_DATABASE_URL,config.TURSO_AUTH_TOKEN,config.TURSO_SYNC_INTERVAL) if config.TURSO_DATABASE_URL else create_store(config.DATABASE_URL))
jobs = JobQueue(store)
runner = ProcessRunner(store)
orchestrator = PhaseOrchestrator(store,runner)
runner.on_complete = orchestrator.continue_after
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
    duration_ms=int((time.monotonic()-started)*1000)
    activity("http.request","Request completed",method=request.method,path=request.url.path,status=response.status_code,duration_ms=duration_ms,actor=who,client=request.client.host if request.client else "unknown")
    if response.status_code >= 400:
        activity("http.error","API request returned an error",endpoint=request.url.path,method=request.method,status=response.status_code,duration_ms=duration_ms,actor=who,client=request.client.host if request.client else "unknown")
    if request.url.path in {"/", "/assets/app.js"} or request.url.path.startswith("/tasks/"):
        response.headers["Cache-Control"]="no-store, max-age=0"
    if response.status_code in (401,403):activity("access.blocked","Unauthorized or forbidden request",method=request.method,path=request.url.path,status=response.status_code,actor=who)
    return response

if config.ADMIN_TOKEN == "dev-admin-token":
    import logging
    logging.getLogger("delivery_api").warning(
        "DELIVERY_ADMIN_TOKEN is unset - using the local development default. "
        "Set DELIVERY_ADMIN_TOKEN before exposing this service."
    )

_stream_shutdown_events:set[asyncio.Event]=set()
_previous_sigint=None
_streams_closed_on_signal=0

def _recorded_engine_session(task_id:str)->str|None:
    for event in reversed(store.events(task_id)):
        match=__import__('re').search(r"(?:^|\s)session:\s*([A-Za-z0-9._-]+)",event.get("message", ""))
        if match:return match.group(1)
    return None


async def _resume_orphaned_handoffs()->None:
    """Repair a durable completed boundary whose next subprocess never started."""
    from .orchestrator import NEXT
    for task in store.list_tasks():
        task_id=task["task_id"]
        if task_id in runner.processes:continue
        states={phase["phase_key"]:phase["state"] for phase in task["phases"]}
        phase_order={phase["phase_key"]:index for index,phase in enumerate(task["phases"])}
        candidates=[(source,target) for source,target in NEXT.items()
                    if states.get(source) in ("approved","done") and states.get(target) in ("pending","running")]
        if not candidates:continue
        source,target=max(candidates,key=lambda item:phase_order[item[0]])
        async def recover(task_id=task_id,source=source,target=target):
            try:
                activity("orchestrator.recover","Retrying orphaned completed handoff",task_id=task_id,completed_phase=source,next_phase=target)
                await orchestrator.continue_after(task_id,source)
            except Exception as exc:
                message=f"Error: completed {source} could not launch {target}. Likely cause: {type(exc).__name__}: {exc}. Next: fix the named runtime/artifact issue and restart Requi.re; the completed phase is preserved."
                store.add_event(task_id,"error",message,phase_key=target,payload={"cause":f"{type(exc).__name__}: {exc}","next":"Fix the named runtime/artifact issue and restart Requi.re; the completed phase is preserved.","completed_phase":source,"next_phase":target})
                activity("orchestrator.recover.failed","Orphaned completed handoff still blocked",task_id=task_id,completed_phase=source,next_phase=target,error=f"{type(exc).__name__}: {exc}")
        asyncio.create_task(recover())


def _replayable_decisions(task_id:str)->dict[str,dict[str,str]]:
    """Exact typed answers accepted before a restart, keyed by contract identity."""
    from .runner import decision_fingerprint
    decisions:dict[str,tuple[int,dict]]={};result={}
    for event in store.events(task_id):
        decision=(event.get("payload") or {}).get("decision")
        if event.get("kind")=="founder_decision" and isinstance(decision,dict) and decision.get("decision_id"):
            decision_id=str(decision["decision_id"]);decisions[decision_id]=(event["id"],decision);result.pop(decision_id,None)
        elif event.get("kind")=="answer":
            decision_id=str((event.get("payload") or {}).get("decision_id") or "")
            prior=decisions.get(decision_id)
            if prior and prior[0] < event["id"]:
                answer=(event.get("payload") or {}).get("answer") or event.get("message","").removeprefix("You: ")
                result[decision_id]={"answer":answer,"fingerprint":decision_fingerprint(prior[1])}
    return result


async def _resume_interrupted_interviews()->None:
    """Reattach persisted interview checkpoints after a rebuild/restart."""
    for task in store.list_tasks():
        if task["task_id"] in runner.processes:continue
        events=store.events(task["task_id"])
        latest=events[-1] if events else {}
        # Older builds classified their own SIGTERM during replacement as a
        # failed engine run. Accept that one precise legacy state too, so an
        # in-progress interview survives the first upgrade to this version.
        legacy_restart=(task["state"]=="failed" and latest.get("kind")=="error" and latest.get("payload",{}).get("exit_code") in (-15,143))
        gate_parked=task["state"]=="waiting_gate"
        if task["state"]!="running" and not legacy_restart and not gate_parked:continue
        session=_recorded_engine_session(task["task_id"])
        if not session:continue
        built=os.environ.get("REQUI_RE_DIST")=="1"
        engine_dir=(Path.cwd() if built else Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve())
        try:runtime=(__import__('delivery_api.engine_runtime',fromlist=['EngineRuntime']).EngineRuntime(Path(sys.executable),(),((Path(sys.executable),True),)) if built else await resolve_engine_runtime(engine_dir))
        except Exception as e:
            activity("engine.resume.failed","Interrupted interview could not resume",task_id=task["task_id"],session=session,error=f"{type(e).__name__}: {str(e)[:180]}")
            continue
        async def resume(task_id=task["task_id"],session=session,runtime=runtime,engine_dir=engine_dir,built=built):
            activity("engine.resume","Resuming interrupted or gate-parked interview from checkpoint",task_id=task_id,session=session,prior_state=(store.task(task_id) or {}).get("state"))
            await runner.run(task_id,[str(runtime.python),"-m","interview_engine.cli","--session",session],cwd=engine_dir,phase="intent",env={"OLLAMA_NUM_PARALLEL":os.environ.get("OLLAMA_NUM_PARALLEL","1"),"PYTHONUNBUFFERED":"1",**({} if built else {"PYTHONPATH":str(engine_dir/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})},completion_phases=("intent","conops"),replay_decisions=_replayable_decisions(task_id))
        asyncio.create_task(resume())


@app.on_event("startup")
async def _start():
    global _previous_sigint
    config.validate_auth_config()
    from .observability import LOG_PATH,stage_log_paths
    print(f"Server log: {LOG_PATH.resolve()}",file=sys.stderr)
    for phase,path in stage_log_paths().items():print(f"Stage log [{phase}]: {path.resolve()}",file=sys.stderr)
    await jobs.start()
    await _resume_orphaned_handoffs()
    await asyncio.sleep(0)
    await _resume_interrupted_interviews()
    # Uvicorn waits for open HTTP connections before lifespan shutdown. Close
    # SSE at signal time, then delegate to uvicorn's handler; a second SIGINT
    # still reaches uvicorn and forces its standard immediate exit.
    if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGINT) is not _shutdown_signal:
        _previous_sigint=signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT,_shutdown_signal)

def _shutdown_signal(signum,frame):
    global _streams_closed_on_signal
    count=len(_stream_shutdown_events);_streams_closed_on_signal+=count
    activity("server.shutdown.signal","Server shutdown signal received",streams=count,signal="SIGINT")
    for event in tuple(_stream_shutdown_events):event.set()
    if callable(_previous_sigint):_previous_sigint(signum,frame)

@app.on_event("shutdown")
async def _stop():
    stream_count=_streams_closed_on_signal+len(_stream_shutdown_events)
    activity("server.shutdown.signal","Server shutdown received",streams=stream_count,engines=len(runner.processes))
    for event in tuple(_stream_shutdown_events):event.set()
    await runner.shutdown()
    await jobs.stop()
    activity("server.shutdown.complete","Server shutdown complete",streams_closed=stream_count)

@app.get("/", include_in_schema=False)
def index(): return FileResponse(WEB_DIR / "index.html")

@app.get("/api/auth")
def auth_status(request: Request):
    a = auth.actor(request)
    mode="basic" if config.BASIC_AUTH_ENABLED else ("oauth" if auth.enabled() else "local")
    return {"mode":mode,"authenticated":a.id not in {"anonymous"},"actor":a.label,"admin":a.admin,
            "providers":[p for p,ident in (("google",config.GOOGLE_CLIENT_ID),("github",config.GITHUB_CLIENT_ID)) if ident],"workspace":"shared"}

class ClientLog(BaseModel):
    kind:str=Field(max_length=80)
    message:str=Field(max_length=16000)
    path:str=Field(max_length=1000)
    detail:dict={}

@app.post("/api/client-log",status_code=204)
def client_log(request:Request,body:ClientLog):
    activity("client."+body.kind,body.message,path=body.path,actor=auth.actor(request).label,client=request.client.host if request.client else "unknown",**body.detail)

class BasicCredentials(BaseModel):
    username: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=256)

def _auth_response(result):
    actor,sid=result;response=JSONResponse({"status":"signed_in","actor":actor.label})
    response.set_cookie(config.SESSION_COOKIE,sid,max_age=config.SESSION_TTL_SECONDS,httponly=True,secure=config.COOKIE_SECURE,samesite="lax",path="/");return response

@app.get("/auth/email-availability")
def email_availability(request:Request,email:str=Query(min_length=3,max_length=254)):
    normalized=auth.normalize_email(email);valid=auth.valid_email(normalized);available=valid and request.app.state.store.basic_user(normalized) is None
    activity("auth.email.check","Email availability checked",email=normalized,valid=valid,available=available)
    return {"email":normalized,"valid":valid,"available":available}

@app.post("/auth/signup")
def signup(request:Request,body:BasicCredentials):
    activity("auth.signup.attempt","Signup attempted",username=body.username,client=request.client.host if request.client else "unknown")
    try:result=auth.basic_signup(request,body.username,body.password);activity("auth.signup.success","Account created",username=body.username);return _auth_response(result)
    except HTTPException as e:activity("auth.signup.failure","Signup rejected",username=body.username,status=e.status_code,reason=(e.detail.get("error") if isinstance(e.detail,dict) else e.detail));raise

@app.post("/auth/signin")
def signin(request:Request,body:BasicCredentials):
    activity("auth.signin.attempt","Sign-in attempted",username=body.username,client=request.client.host if request.client else "unknown")
    try:result=auth.basic_signin(request,body.username,body.password);activity("auth.signin.success","Sign-in succeeded",username=body.username);return _auth_response(result)
    except HTTPException as e:activity("auth.signin.failure","Sign-in rejected",username=body.username,status=e.status_code,reason=(e.detail.get("error") if isinstance(e.detail,dict) else e.detail));raise

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
    current=auth.actor(request);activity("auth.signout","Sign-out requested",actor=current.label)
    sid=request.cookies.get(config.SESSION_COOKIE)
    if sid: store.delete_session(sid)
    from fastapi.responses import JSONResponse
    response=JSONResponse({"status":"signed_out"}); response.delete_cookie(config.SESSION_COOKIE,path="/"); return response

@app.get("/about",include_in_schema=False)
def about_page():return FileResponse(WEB_DIR / "index.html")

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse(WEB_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/tasks/{task_id}/view", include_in_schema=False)
def task_view(task_id: str): return FileResponse(WEB_DIR / "index.html")

@app.get("/sessions", include_in_schema=False)
def sessions_view(): return FileResponse(WEB_DIR / "index.html")

@app.get("/sessions/{session_id}/view", include_in_schema=False)
def session_view(session_id: str): return FileResponse(WEB_DIR / "index.html")

@app.get("/sessions/{session_id}/{phase}", include_in_schema=False)
def session_phase_view(session_id: str, phase: str): return FileResponse(WEB_DIR / "index.html")

@app.get("/api/sessions")
def sessions(): return {"sessions":bundles.discover(),"artifacts_root":str(bundles.root())}

@app.get("/api/settings/engine-python")
async def engine_python_settings(a:auth.Actor=Depends(auth.require_actor)):
    engine_dir=Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve()
    candidates=await discover_engine_runtimes(engine_dir)
    return {"path":str(saved_python() or ""),"candidates":[str(x) for x in candidates],"count":len(candidates)}
class EnginePythonRequest(BaseModel):path:str
@app.post("/api/settings/engine-python")
async def set_engine_python(req:EnginePythonRequest,a:auth.Actor=Depends(auth.require_actor)):
    engine_dir=Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve()
    try:path=await save_python(req.path,engine_dir)
    except ValueError as e:raise HTTPException(400,{"error":"Engine Python could not be saved.","cause":str(e),"next":"Choose one of the detected Python environments that passes the dependency check."})
    activity("engine.settings","Engine Python saved",actor=a.label,python=str(path));return {"path":str(path),"status":"saved"}

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

@app.post("/api/tasks/{task_id}/start")
async def start_task_engine(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,{"error":"Task not found.","cause":"It may have been deleted.","next":"Return to Home and choose another task."})
    if task["state"]!="not_started":raise HTTPException(409,{"error":"This task already started.","cause":f"Its current state is {task['state']}.","next":"Open its live progress instead of starting it again."})
    idea=(task.get("brief") or task.get("title") or "").strip()
    if not idea:raise HTTPException(400,{"error":"The interview needs a starting idea.","cause":"This task has no brief.","next":"Add a sentence describing the product, then start again."})
    built_runtime=os.environ.get("REQUI_RE_DIST")=="1"
    engine_dir=(Path.cwd() if built_runtime else Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve())
    if not built_runtime and not engine_dir.is_dir():raise HTTPException(503,{"error":"Interview engine is not available.","cause":f"Expected it at {engine_dir}.","next":"Run scripts/pull-all.sh, then restart the UI."})
    try:runtime=(__import__('delivery_api.engine_runtime',fromlist=['EngineRuntime']).EngineRuntime(Path(sys.executable),(),((Path(sys.executable),True),)) if built_runtime else await resolve_engine_runtime(engine_dir))
    except EngineRuntimeError as e:
        activity("engine.preflight.failed","Interview engine dependency preflight failed",task_id=task_id,actor=a.label,module=e.module,checked="; ".join(f"{path}={'ok' if ok else 'missing'}" for path,ok in e.attempts))
        command=f'python -m pip install -e "{engine_dir}"'
        raise HTTPException(503,{"error":f"Interview engine cannot start because Python module '{e.module}' is missing.","cause":"None of the available engine Python environments contains the interview runtime dependencies.","next":f"Activate the environment that runs interview-engine, then run: {command}. To select it explicitly, set INTERVIEW_ENGINE_PYTHON=/path/to/python before starting Requi.re."})
    engine_python=runtime.python
    store.update_phase(task_id,"intent","running",detail="Launching adaptive interview")
    store.audit(a.label,"interview.started",task_id,{"engine_dir":str(engine_dir),"python":str(engine_python)})
    activity("engine.preflight.ok","Interview engine runtime resolved",task_id=task_id,actor=a.label,python=str(engine_python),checked="; ".join(f"{path}={'ok' if ok else 'missing'}" for path,ok in runtime.attempts))
    activity("engine.start","Interview engine launch requested",task_id=task_id,actor=a.label,python=str(engine_python))
    async def execute():
        try:await runner.run(task_id,[str(engine_python),"-m","interview_engine.cli",idea],cwd=engine_dir,phase="intent",env={"OLLAMA_NUM_PARALLEL":os.environ.get("OLLAMA_NUM_PARALLEL","1"),"PYTHONUNBUFFERED":"1",**({} if built_runtime else {"PYTHONPATH":str(engine_dir/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})},completion_phases=("intent","conops"))
        except Exception as e:
            store.update_phase(task_id,"intent","failed",detail=f"Interview engine could not start: {type(e).__name__}: {str(e)[:180]}")
            store.add_event(task_id,"error","Interview engine could not start",phase_key="intent",payload={"cause":str(e)[:300],"next":"Check run.log and the interview-engine installation, then retry."})
            activity("engine.failure","Interview engine failed",task_id=task_id,error=f"{type(e).__name__}: {e}")
    asyncio.create_task(execute())
    return {"status":"started","task_id":task_id,"next":f"/tasks/{task_id}/view"}

class InterviewAnswer(BaseModel):
    answer:str=Field(min_length=1,max_length=8000)
    decision_id:str|None=Field(default=None,max_length=160)
@app.post("/api/tasks/{task_id}/answer")
async def answer_interview(task_id:str,body:InterviewAnswer,a:auth.Actor=Depends(auth.require_actor)):
    task_before=store.task(task_id) or {};input_before=runner.input_state(task_id)
    recent_answers=[{"event_id":event["id"],"phase_key":event.get("phase_key"),"decision_id":event.get("payload",{}).get("decision_id")} for event in store.events(task_id) if event.get("kind")=="answer"][-10:]
    activity("api.answer.attempt","Founder answer submission received",endpoint=f"/api/tasks/{task_id}/answer",task_id=task_id,task_state=task_before.get("state"),active_phase=task_before.get("active_phase"),submitted_decision_id=body.decision_id,open_decision_id=(input_before.get("decision") or {}).get("decision_id"),process_live=input_before["process_live"],waiting=input_before["waiting"],recent_answer_events=json.dumps(recent_answers,separators=(",",":")))
    try:await runner.send_input(task_id,body.answer,body.decision_id)
    except RuntimeError as e:
        task=store.task(task_id) or {};state=runner.input_state(task_id);decision=state.get("decision") or {}
        cause=(f"The task is {task.get('state','unknown')} in phase {task.get('active_phase','unknown')}; "
               f"process_live={state['process_live']}, waiting={state['waiting']}. The submitted decision is no longer open.")
        next_step=("The current engine question changed. Refreshing the task will show the live state; answer only the current prompt."
                   if state["process_live"] else "The engine process has ended or restarted. Refresh the task; resume/restart the active phase instead of submitting this stale dialog.")
        message="Stale founder decision was not submitted"
        payload={"endpoint":f"/api/tasks/{task_id}/answer","http_status":409,"task_state":task.get("state"),"active_phase":task.get("active_phase"),"decision_id":body.decision_id or decision.get("decision_id"),"open_decision_id":decision.get("decision_id"),"recent_answer_events":recent_answers,"process_live":state["process_live"],"waiting":state["waiting"],"reason":str(e),"cause":cause,"next":next_step}
        store.add_event(task_id,"error",f"Error: {message}. Likely cause: {cause} Next: {next_step}",phase_key=task.get("active_phase"),payload=payload)
        activity("api.answer.stale",message,task_id=task_id,phase=task.get("active_phase"),**payload)
        raise HTTPException(409,{"error":message+".","cause":cause,"next":next_step,"stale":True,"current_state":task.get("state"),"active_phase":task.get("active_phase")})
    active_phase=(store.task(task_id) or {}).get("active_phase") or "intent"
    store.add_event(task_id,"answer",f"You: {body.answer}",phase_key=active_phase,payload={"actor":a.label,"character_count":len(body.answer),"transcript":True,"decision_id":body.decision_id,"answer":body.answer})
    activity("engine.answer","Founder answer submitted",task_id=task_id,phase=active_phase,actor=a.label,character_count=len(body.answer))
    return {"status":"accepted"}

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = store.task(task_id)
    if not task: raise HTTPException(404, "task not found")
    return {**task, "events": store.events(task_id), "artifacts": store.task_artifacts(task_id), "input_state":runner.input_state(task_id)}

@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(default=0, ge=0)):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    return {"events": store.events(task_id, after)}

@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str, request: Request):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    stop=asyncio.Event();_stream_shutdown_events.add(stop)
    async def generate():
        try:
            existing = store.events(task_id)
            cursor = existing[-1]["id"] if existing else 0
            while not stop.is_set() and not await request.is_disconnected():
                events = store.events(task_id, cursor)
                for event in events:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                yield f"event: heartbeat\ndata: {json.dumps({'task_id': task_id})}\n\n"
                try:await asyncio.wait_for(stop.wait(),2)
                except asyncio.TimeoutError:pass
        finally:
            _stream_shutdown_events.discard(stop)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

class RerunRequest(BaseModel):
    from_phase: str
    confirm: bool = False

@app.post("/api/tasks/{task_id}/rerun")
def rerun(task_id: str, req: RerunRequest, request: Request, a: auth.Actor = Depends(auth.require_actor)):
    task=store.task(task_id)
    if not task: raise HTTPException(404, "task not found")
    try:missing=store.rerun_prerequisite(task_id,req.from_phase)
    except KeyError:raise HTTPException(409,{"error":f"Cannot rerun from unknown stage '{req.from_phase}'.","cause":"That stage is not part of this task.","next":"Choose one of the stages shown in the V-process."})
    if missing:
        raise HTTPException(409,{"error":f"Cannot rerun from {req.from_phase}: prerequisite '{missing['label']}' is not complete.","cause":"Stages are sequential; rerunning a downstream stage without its upstream result would produce ungrounded output.","next":f"Run or rerun '{missing['label']}' first, then retry {req.from_phase}."})
    if not req.confirm:
        downstream = [p["phase_key"] for p in task["phases"] if p["position"] >= next(p["position"] for p in task["phases"] if p["phase_key"] == req.from_phase)]
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
async def decide_gate(task_id:str,gate_key:str,req:GateDecision,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    try:
        gate=store.decide_gate(task_id,gate_key,req.action,selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,actor=a.label)
        phase=gate["after_phase"]
        if req.action=="redo":asyncio.create_task(orchestrator.launch(task_id,phase,redo=True))
        else:asyncio.create_task(orchestrator.continue_after(task_id,phase))
        return gate
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
def health(): return {"ok": True, "total_spend_usd": store.total_spend(), "tasks": len(store.list_tasks()), **RUNTIME_VERSION}
