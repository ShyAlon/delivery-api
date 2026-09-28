"""Thin job-control API plus the V-model web console, served on one port."""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
import json
import signal
import subprocess
import threading
import logging
import time
import os
import sys
import mimetypes
import zipfile
import io
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from vmodel_contracts import RunState, RunStatus

from . import config
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from . import auth
from .jobs import JobQueue
from .runner import ProcessRunner
from .orchestrator import PhaseOrchestrator,ExecutionMode
from .engine_runtime import EngineRuntimeError, resolve_engine_runtime, discover_engine_runtimes, save_python, saved_python
from .engine_client import HttpEngineClient,EngineClientError,EngineUnreachableError
from vmodel_contracts.interactive import InputKind,InputRequest,InputResponse,ProcessStatus,StartSessionRequest,EngineSnapshot,ModelRouteSwitchRequest
from .store import create_store
from .simulation import Simulation, ImportValidationError
from . import bundles
from . import model_routes as model_route_lib
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
UI_MODE_SCHEMA=json.loads((WEB_DIR / "ui-modes.json").read_text())
UI_MODES=frozenset(UI_MODE_SCHEMA)
store = (__import__('delivery_api.store',fromlist=['TursoReplicaStore']).TursoReplicaStore(str(config.local_database_path()),config.TURSO_DATABASE_URL,config.TURSO_AUTH_TOKEN,config.TURSO_SYNC_INTERVAL) if config.TURSO_DATABASE_URL else create_store(config.DATABASE_URL))
jobs = JobQueue(store)
runner = ProcessRunner(store)
orchestrator = PhaseOrchestrator(store,runner)
runner.on_complete = orchestrator.continue_after
simulation = Simulation(store)
engine_client = HttpEngineClient(config.INTERVIEW_ENGINE_URL,token=config.INTERVIEW_ENGINE_TOKEN)

ENGINE_START_HINT="cd ../interview-engine && .venv/bin/python -m uvicorn interview_engine.http_service:app --host 127.0.0.1 --port 8010"
def _engine_unreachable_payload(exc:EngineUnreachableError)->dict:
    return {"error":"The interview engine is not reachable.",
        "cause":f"Nothing accepted the connection at {exc.base_url} - the engine service is not running (crashed, never started, or a stale port).",
        "next":f"Restart scripts/run-ui.sh (it starts the engine automatically), or start the engine by hand: {ENGINE_START_HINT}. Then retry."}
def _engine_unreachable(exc:EngineUnreachableError)->HTTPException:
    return HTTPException(503,_engine_unreachable_payload(exc))
log = logging.getLogger("delivery_api.http")



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
    # The typed HTTP cutover stores this binding at session creation. It is
    # authoritative; event/path scanning is legacy recovery only.
    recorded=store.engine_session(task_id)
    if recorded:return recorded
    for event in reversed(store.events(task_id)):
        match=__import__('re').search(r"(?:^|\s)session:\s*([A-Za-z0-9._-]+)",event.get("message", ""))
        if match:return match.group(1)
        path=str((event.get("payload") or {}).get("path") or "")
        match=__import__('re').search(r"(?:^|[/\\])artifacts[/\\]([A-Za-z0-9._-]+)(?:[/\\]|$)",path)
        if match:return match.group(1)
    return None


def _reconcile_approved_prefixes()->None:
    """Repair earlier phase rows contradicted by a durable downstream approval."""
    for task in store.list_tasks():
        phases=task.get("phases",[])
        approved=[index for index,phase in enumerate(phases) if phase.get("state") in ("approved","done")]
        if not approved:continue
        furthest=max(approved)
        for phase in phases[:furthest]:
            if phase.get("state") not in ("approved","done"):
                store.update_phase(task["task_id"],phase["phase_key"],"approved",detail="Recovered from durable downstream approval")
                activity("orchestrator.reconcile","Repaired contradictory earlier phase state",task_id=task["task_id"],phase=phase["phase_key"],evidence_phase=phases[furthest]["phase_key"])


async def _resume_orphaned_handoffs()->None:
    """Repair a durable completed boundary whose next subprocess never started."""
    from .phases import PHASE_DEFINITIONS, successor
    _reconcile_approved_prefixes()
    for task in store.list_tasks():
        task_id=task["task_id"]
        if task_id in runner.processes:continue
        states={phase["phase_key"]:phase["state"] for phase in task["phases"]}
        candidates=[]
        for definition in PHASE_DEFINITIONS:
            target=successor(definition.key)
            if target and states.get(definition.key) in ("approved","done") and states.get(target.key) in ("pending","running"):
                candidates.append((definition.key,target.key))
        if not candidates:continue
        source,target=candidates[-1]
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
    """Pre-HTTP interview sessions cannot be text-replayed safely."""
    for task in store.list_tasks():
        if task.get("state") not in ("running","waiting_gate"):continue
        phases={p["phase_key"]:p["state"] for p in task.get("phases",[])}
        if all(phases.get(p) in ("approved","done") for p in ("intent","conops")):continue
        if store.engine_session(task["task_id"]):continue
        if _recorded_engine_session(task["task_id"]):
            store.add_event(task["task_id"],"incompatible","Pre-HTTP interview requires restart",phase_key="intent",payload={"process_status":int(ProcessStatus.INCOMPATIBLE),"next_step":60})


def _legacy_external_requirement(task:dict,events:list[dict])->dict|None:
    """Migrate pre-ActiveWork architecture pauses from durable phase/event facts."""
    architecture=next((phase for phase in task.get("phases",[]) if phase.get("phase_key")=="architecture"),{})
    paused=architecture.get("state")=="awaiting_approval" and "baseline required" in architecture.get("detail","").lower()
    legacy_event=next((event for event in reversed(events) if event.get("phase_key")=="architecture" and "SystemBaseline file" in event.get("message","")),None)
    if not (paused or legacy_event):return None
    try:path=str(orchestrator._baseline_path(task["task_id"]))
    except Exception:return None
    return {"kind":"external_input","title":"Existing-system baseline required","summary":"Architecture is paused until the real system facts are supplied.","path":path,"format":"SystemBaseline JSON","required_fields":["baseline_id","system_name","mode: existing","repositories (at least one)"],"next":"Save a valid system-baseline.json at this path, or correct the earlier choice to Greenfield."}


def _reconcile_terminal_http_interview(task_id:str,state:EngineSnapshot)->bool:
    """Repair a lost delivery handoff only with a bound terminal session and valid files."""
    if state.process_status!=ProcessStatus.COMPLETED or state.task_id!=task_id:return False
    if store.engine_session(task_id)!=state.session_id:return False
    task=store.task(task_id)
    if not task or task["state"] not in ("running","failed") or task["active_phase"] not in ("intent","conops"):
        return False
    if next((phase["state"] for phase in task["phases"] if phase["phase_key"]=="conops"),None) in ("approved","done"):
        return False
    from vmodel_contracts.runtime import artifacts_root
    from vmodel_contracts.interview import IntentModel
    from vmodel_contracts.conops import ConOpsDocument
    interview=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/state.session_id/"interview"
    try:
        intent=IntentModel.model_validate_json((interview/"intent_model.json").read_text())
        conops=ConOpsDocument.model_validate_json((interview/"conops.json").read_text())
        if intent.session_id!=state.session_id or conops.session_id!=state.session_id:
            raise ValueError("interview artifacts belong to a different session")
    except (OSError,ValueError) as exc:
        activity("engine.recovery.artifacts_invalid","Completed interview cannot be handed off without verified artifacts",task_id=task_id,session_id=state.session_id,error=str(exc)[:350])
        return False
    return store.reconcile_completed_interview(task_id,state.session_id,state.sequence)

async def _continue_reconciled_interview(task_id:str)->None:
    try:
        await orchestrator.continue_after(task_id,"conops")
        activity("engine.recovery.handoff","Interview handoff to Stakeholder needs returned",task_id=task_id,next_phase="needs",task_state=(store.task(task_id) or {}).get("state"),active_phase=(store.task(task_id) or {}).get("active_phase"))
    except Exception as exc:
        store.add_event(task_id,"error","Completed interview reconciled but the next phase could not start; restart delivery to retry.",phase_key="needs",
            payload={"completed_phase":"conops","next_phase":"needs","cause":f"{type(exc).__name__}: {exc}"})
        activity("orchestrator.recover.failed","Reconciled interview handoff failed",task_id=task_id,error=str(exc)[:350])

async def _recover_tasks()->None:
    """Apply one pure recovery plan per task; never assign competing owners."""
    from .recovery import plan_recovery
    _reconcile_approved_prefixes()
    for task in store.list_tasks():
        task_id=task["task_id"]
        if runner.work_state(task_id) is not None:continue
        events=store.events(task_id);session=_recorded_engine_session(task_id)
        # The linked HTTP session owns interview state. Reconcile its verified
        # terminal boundary; never replay its answers through the legacy runner.
        if store.engine_session(task_id):
            try:
                state=await run_in_threadpool(engine_client.snapshot,session)
            except EngineClientError as exc:
                if exc.http_status==422 and "legacy session has no pinned model route" in exc.error.detail.lower():
                    activity("engine.recovery.legacy_unpinned","Historical interview cannot be restored under session-owned routing; preserving it",task_id=task_id,session_id=session,status=exc.http_status)
                else:
                    activity("engine.recovery.snapshot_unavailable","Could not classify linked interview during recovery",task_id=task_id,session_id=session,status=exc.http_status,error=type(exc).__name__)
                continue
            except EngineUnreachableError as exc:
                activity("engine.recovery.snapshot_unavailable","Could not classify linked interview during recovery",task_id=task_id,session_id=session,error=type(exc).__name__)
                continue
            if state.task_id!=task_id or state.session_id!=session:
                activity("engine.recovery.session_mismatch","Linked interview belongs to another task or session",task_id=task_id,session_id=session)
                continue
            if state.process_status==ProcessStatus.COMPLETED:
                repaired=_reconcile_terminal_http_interview(task_id,state)
                current=store.task(task_id)
                phases={phase["phase_key"]:phase["state"] for phase in current["phases"]}
                # A crash after the durable reconciliation but before launch
                # leaves the same orphaned handoff as a normal subprocess exit.
                if repaired:
                    activity("engine.recovery.reconciled","Restored completed interview boundary; queuing Stakeholder needs",task_id=task_id,session_id=session,sequence=state.sequence,intent=phases["intent"],conops=phases["conops"],next_phase="needs")
                if repaired or (phases["conops"] in ("approved","done") and phases["needs"] in ("pending","running")
                                and current["active_phase"]=="conops"
                                and any(event["kind"]=="progress" and (event.get("payload") or {}).get("source")=="terminal_engine_snapshot"
                                        and (event.get("payload") or {}).get("session_id")==session for event in events)):
                    if not repaired:activity("engine.recovery.handoff_retry","Retrying reconciled interview handoff",task_id=task_id,session_id=session,next_phase="needs")
                    asyncio.create_task(_continue_reconciled_interview(task_id))
                else:
                    now=store.task(task_id)
                    phase_states={phase["phase_key"]:phase["state"] for phase in now["phases"]}
                    path=__import__('vmodel_contracts.runtime',fromlist=['artifacts_root']).artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"
                    if now["active_phase"] not in ("intent","conops"):
                        reason="downstream_already_active"
                    elif phase_states["conops"] in ("approved","done"):
                        reason="conops_already_approved"
                    elif now["state"] not in ("running","failed"):
                        reason="task_state_not_reconcilable"
                    elif not (path/"intent_model.json").is_file() or not (path/"conops.json").is_file():
                        reason="canonical_artifacts_missing"
                    else:reason="artifact_validation_or_store_guard"
                    activity("engine.recovery.completed","Completed HTTP interview classified; no handoff required or available",task_id=task_id,session_id=session,reason=reason,task_state=now["state"],active_phase=now["active_phase"],intent=phase_states["intent"],conops=phase_states["conops"],needs=phase_states["needs"])
                continue
            if state.process_status==ProcessStatus.WAITING_INPUT and state.open_input is not None:
                continue
            # A linked HTTP session is never the legacy subprocess recovery
            # target. The engine owns its live checkpoint and typed inputs.
            activity("engine.recovery.linked","Linked HTTP interview remains engine-owned",task_id=task_id,session_id=session,status=int(state.process_status))
            continue
        legacy_requirement=_legacy_external_requirement(task,events)
        existing_requirement=next((event for event in reversed(events) if event.get("kind")=="requirement" and (event.get("payload") or {}).get("kind")=="external_input"),None)
        if legacy_requirement and not existing_requirement:
            store.add_event(task_id,"requirement","Architecture paused for an existing SystemBaseline file",phase_key="architecture",payload=legacy_requirement)
            events=store.events(task_id)
        plan=plan_recovery({**task,"events":events},events[-1] if events else None,has_session=bool(session))
        if plan.action=="reconcile":
            _reconcile_approved_prefixes();task=store.task(task_id);events=store.events(task_id)
            plan=plan_recovery({**task,"events":events},events[-1] if events else None,has_session=bool(session))
        if plan.action=="launch_successor":
            async def launch(task_id=task_id,phase=plan.phase):
                try:
                    activity("orchestrator.recover","Retrying orphaned completed handoff",task_id=task_id,completed_phase=phase,next_phase=plan.target)
                    await orchestrator.continue_after(task_id,phase)
                except Exception as exc:
                    message=f"Error: completed {phase} could not launch {plan.target}. Likely cause: {type(exc).__name__}: {exc}. Next: fix the named runtime/artifact issue and restart Requi.re; the completed phase is preserved."
                    store.add_event(task_id,"error",message,phase_key=plan.target,payload={"cause":f"{type(exc).__name__}: {exc}","next":"Fix the named runtime/artifact issue and restart Requi.re; the completed phase is preserved.","completed_phase":phase,"next_phase":plan.target})
            asyncio.create_task(launch())
        elif plan.action=="restore_gate":
            gate=store.wait_at_gate(task_id,plan.phase or task.get("active_phase"))
            activity("gate.owner.restored","Restored founder approval gate",task_id=task_id,phase=plan.phase,gate_key=(gate or {}).get("gate_key"))
        elif plan.action=="restore_wait":
            requirement_event=next((event for event in reversed(events) if event.get("kind")=="requirement" and (event.get("payload") or {}).get("kind")=="external_input"),{})
            requirement=(requirement_event.get("payload") or {})
            runner.pause_for_requirement(task_id,plan.phase or task.get("active_phase") or "architecture",requirement)
        elif plan.action=="resume" and session:
            await _resume_one_interview(task_id,session)

async def _resume_one_interview(task_id:str,session:str)->None:
    store.add_event(task_id,"incompatible","Pre-HTTP interview requires restart",phase_key="intent",payload={"process_status":int(ProcessStatus.INCOMPATIBLE),"next_step":60,"legacy_session":session})


@asynccontextmanager
async def lifespan(application:FastAPI):
    global _previous_sigint
    config.validate_auth_config()
    from .observability import LOG_PATH,stage_log_paths
    print(f"Server log: {LOG_PATH.resolve()}",file=sys.stderr)
    for phase,path in stage_log_paths().items():print(f"Stage log [{phase}]: {path.resolve()}",file=sys.stderr)
    await jobs.start();await _recover_tasks()
    if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGINT) is not _shutdown_signal:
        _previous_sigint=signal.getsignal(signal.SIGINT);signal.signal(signal.SIGINT,_shutdown_signal)
    try:yield
    finally:
        stream_count=_streams_closed_on_signal+len(_stream_shutdown_events)
        activity("server.shutdown.signal","Server shutdown received",streams=stream_count,engines=len(runner.processes))
        for event in tuple(_stream_shutdown_events):event.set()
        await runner.shutdown();await jobs.stop()
        activity("server.shutdown.complete","Server shutdown complete",streams_closed=stream_count)

def _shutdown_signal(signum,frame):
    global _streams_closed_on_signal
    count=len(_stream_shutdown_events);_streams_closed_on_signal+=count
    activity("server.shutdown.signal","Server shutdown signal received",streams=count,signal="SIGINT")
    for event in tuple(_stream_shutdown_events):event.set()
    if callable(_previous_sigint):_previous_sigint(signum,frame)


app = FastAPI(title="Requi.re API", version="0.2.0", lifespan=lifespan)
app.state.store = store
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
    if request.url.path == "/" or request.url.path.startswith("/assets/") or request.url.path.startswith("/tasks/"):
        response.headers["Cache-Control"]="no-store, max-age=0"
    if response.status_code in (401,403):activity("access.blocked","Unauthorized or forbidden request",method=request.method,path=request.url.path,status=response.status_code,actor=who)
    return response

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
def start_run(req: StartRequest, request: Request, a: auth.Actor = Depends(auth.require_run_start)):
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

class TaskStartRequest(BaseModel):
    model_route:Literal["ollama","deepseek"]|None=None
    initial_model:str|None=None
@app.get("/api/model-routes")
def model_routes(a:auth.Actor=Depends(auth.require_actor)):
    """The engine resolves routes; legacy env keys only seed the initial choice."""
    routes=[]
    try:default=engine_client.default_route()
    except (EngineClientError,EngineUnreachableError,AttributeError):default=None
    for route in ("ollama","deepseek"):
        try:
            initial_model=(default or {}).get("model") if route==(default or {}).get("route") else None
            result=engine_client.preview_route(route,initial_model=initial_model) if initial_model else engine_client.preview_route(route)
            models=result["model_map"]
            pairs={tuple(pair) for pair in models.values()}
            if len(pairs)!=1:raise ValueError("engine preview returned divergent stage models")
            provider,model=next(iter(pairs))
            if provider!=route:raise ValueError(f"engine preview returned {provider} for selected {route}")
            options=[model]
            if route=="ollama":
                try:options=engine_client.local_models()
                except (EngineClientError,EngineUnreachableError,AttributeError):pass
            routes.append({"id":route,"available":True,"model":f"{provider}:{model}","model_map":models,"models":options})
        except (EngineClientError,EngineUnreachableError,KeyError,ValueError,TypeError) as exc:
            reason=exc.error.detail if isinstance(exc,EngineClientError) else str(exc)
            if route=="ollama":
                try:
                    installed=engine_client.local_models()
                    if installed:
                        routes.append({"id":route,"available":True,"model":None,"models":installed,"error":f"default unavailable: {reason}"})
                        continue
                except (EngineClientError,EngineUnreachableError,AttributeError):pass
            routes.append({"id":route,"available":False,"error":reason})
    return {"routes":routes,"default_route":default}
@app.post("/api/tasks/{task_id}/start")
async def start_task_engine(task_id:str,body:TaskStartRequest=TaskStartRequest(),a:auth.Actor=Depends(auth.require_run_start)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    if task["state"]!="not_started":raise HTTPException(409,"task already started")
    idea=(task.get("brief") or task.get("title") or "").strip()
    # A missing UI choice uses the historical initial default, once, at creation.
    selected_route=body.model_route
    initial_model=None
    if selected_route is None:
        try:
            initial=await run_in_threadpool(engine_client.default_route)
            selected_route,initial_model=initial['route'],initial.get('model')
        except EngineClientError as exc:raise HTTPException(exc.http_status,exc.error.model_dump(mode="json"))
        except EngineUnreachableError as exc:raise _engine_unreachable(exc)
    elif body.initial_model:
        try:initial=await run_in_threadpool(engine_client.default_route)
        except EngineClientError as exc:raise HTTPException(exc.http_status,exc.error.model_dump(mode="json"))
        except EngineUnreachableError as exc:raise _engine_unreachable(exc)
        if selected_route!=initial.get("route") or body.initial_model!=initial.get("model"):
            raise HTTPException(422,"initial model must match the engine's current new-session default")
        initial_model=body.initial_model
    # Same engine resolver as the Start screen; never infer a model from the
    # route name or a second copy of the .env in delivery.
    try:
        # Legacy initial model is preflighted by the engine during Start, not
        # guessed from a separate catalog preview that could name a missing model.
        preview=await run_in_threadpool(engine_client.preview_route,selected_route) if not initial_model else None
        effective_models=preview["model_map"] if preview else {stage:[selected_route,initial_model] for stage in ("probing","distillation","phrasing","scope_tiebreak","expert_opinion","conops")}
        pairs={tuple(pair) for pair in effective_models.values()}
        if len(pairs)!=1:raise ValueError("engine route preview returned divergent stage models")
        provider,model=next(iter(pairs))
        if provider!=selected_route:raise ValueError(f"engine preview returned {provider} for selected {selected_route}")
    except EngineClientError as exc:
        store.add_event(task_id,"model_preflight",f"Interview route {selected_route} rejected: {exc.error.detail}",phase_key="intent",payload={"route":selected_route,"protocol_error":exc.error.model_dump(mode="json")})
        raise HTTPException(exc.http_status,{"error":"Interview route rejected.","cause":exc.error.detail,"next":"Correct the named model settings and reload before starting."})
    except EngineUnreachableError as exc:raise _engine_unreachable(exc)
    except (KeyError,TypeError,ValueError) as exc:raise HTTPException(502,{"error":"Engine model preview was invalid.","cause":str(exc),"next":"Check the engine version and retry."})
    try:started=await run_in_threadpool(engine_client.start,StartSessionRequest(task_id=task_id,idea=idea,model_route=selected_route,initial_model=initial_model))
    except EngineUnreachableError as exc:
        activity("engine.unreachable","Interview engine unreachable at task start",task_id=task_id,base_url=exc.base_url,cause=str(exc.cause))
        store.add_event(task_id,"error",f"Error: the interview engine is not reachable. Likely cause: nothing accepted the connection at {exc.base_url} - the engine service is not running. Next: restart scripts/run-ui.sh (it starts the engine automatically), or start the engine by hand: {ENGINE_START_HINT}. Then retry.",phase_key="intent",payload=_engine_unreachable_payload(exc))
        raise _engine_unreachable(exc)
    except EngineClientError as exc:
        payload={"route":selected_route,"http_status":exc.http_status,"protocol_error":exc.error.model_dump(mode="json"),"stage":"preflight"}
        activity("model.route.preflight.rejected","Interview route rejected by engine",task_id=task_id,**payload)
        store.add_event(task_id,"model_preflight",f"Interview route {selected_route} rejected: {exc.error.detail}",phase_key="intent",payload=payload)
        raise HTTPException(exc.http_status,exc.error.model_dump(mode="json"))
    store.set_engine_session(task_id,started.snapshot.session_id);store.set_model_route(task_id,selected_route)
    effective_models=started.snapshot.model_map or effective_models
    pairs={tuple(pair) for pair in effective_models.values()}
    if len(pairs)!=1:raise HTTPException(502,"engine returned inconsistent session model map")
    provider,model=next(iter(pairs))
    resolved=f"{provider}:{model}"
    store.add_event(task_id,"model_route",f"interview uses {resolved}",phase_key="intent",payload={"route":selected_route,"resolved":resolved,"provider":provider,"model":model,"effective_stages":list(effective_models),"scope":"start","actor":a.label})
    activity("model.route.resolved","Interview route accepted",task_id=task_id,session_id=started.snapshot.session_id,selected=selected_route,effective=resolved)
    detail="Waiting for founder input" if started.snapshot.process_status==ProcessStatus.WAITING_INPUT else "Interview engine running"
    store.update_phase(task_id,"intent","running",detail=detail)
    store.add_event(task_id,"engine_protocol","HTTP interview session started",phase_key="intent",payload=started.model_dump(mode="json"))
    return {"status":"started","task_id":task_id,"session_id":started.snapshot.session_id,"next":f"/tasks/{task_id}/view"}

class RequirementResolution(BaseModel):
    action:str=Field(pattern="^(validate_file|greenfield)$")

@app.post("/api/tasks/{task_id}/requirements/architecture/resolve")
async def resolve_architecture_requirement(task_id:str,body:RequirementResolution,a:auth.Actor=Depends(auth.require_actor)):
    work=runner.work_state(task_id)
    if not work or work.owner!="requirement" or work.phase!="architecture":
        # ActiveWork is an in-memory projection. Recover it from the same
        # durable requirement used by startup before rejecting a valid action.
        events=store.events(task_id)
        requirement_event=next((event for event in reversed(events) if event.get("kind")=="requirement" and event.get("phase_key")=="architecture" and (event.get("payload") or {}).get("kind")=="external_input"),None)
        if requirement_event:
            requirement_id=requirement_event.get("id",0)
            resolved=any(event.get("kind")=="baseline" and event.get("id",0)>requirement_id and (event.get("payload") or {}).get("resolved") for event in events)
            current=runner.work_state(task_id)
            if not resolved and current is None:
                runner.pause_for_requirement(task_id,"architecture",requirement_event.get("payload") or {})
                work=runner.work_state(task_id)
                activity("requirement.owner.restored","Restored requirement owner before resolution",task_id=task_id,action=body.action,requirement_event=requirement_id)
        if not work or work.owner!="requirement" or work.phase!="architecture":
            raise HTTPException(409,{"error":"Architecture is not waiting for an external requirement.","cause":f"Current ActiveWork owner is {getattr(work,'owner',None) or 'none'}.","next":"Reload the task. If Architecture is already running, let it finish; otherwise copy debug info."})
    try:await orchestrator.resolve_baseline_requirement(task_id,body.action)
    except (ValueError,FileNotFoundError) as exc:
        raise HTTPException(422,{"error":"The architecture baseline is not ready.","cause":str(exc),"next":"Fix the SystemBaseline JSON at the shown path, or switch this product to Greenfield."})
    except Exception as exc:
        log.exception("architecture requirement resolution failed task=%s action=%s",task_id,body.action)
        activity("requirement.resolve.failed","Architecture requirement resolution failed",task_id=task_id,action=body.action,error=f"{type(exc).__name__}: {str(exc)[:300]}")
        raise HTTPException(500,{"error":"The architecture baseline could not be resolved.","cause":f"{type(exc).__name__}: {str(exc)[:300]}","next":"Copy debug info and check run.log; the requirement remains recoverable after restart."})
    store.audit(a.label,"requirement.resolved",task_id,{"phase":"architecture","action":body.action})
    return {"status":"resolved","action":body.action}

@app.get("/api/tasks/{task_id}/engine-snapshot")
async def interview_engine_snapshot(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    session=store.engine_session(task_id)
    if not session:raise HTTPException(404,"task has no interview session")
    try:state=await run_in_threadpool(engine_client.snapshot,session)
    except EngineUnreachableError as exc:raise _engine_unreachable(exc)
    except EngineClientError as exc:raise HTTPException(exc.http_status,exc.error.model_dump(mode="json"))
    if state.task_id != task_id:raise HTTPException(409,"session belongs to another task")
    return state.model_dump(mode="json")

class TaskRouteSwitchRequest(BaseModel):
    model_route:Literal["ollama","deepseek"]
    model:str=Field(min_length=1,max_length=160)
    expected_sequence:int=Field(ge=0)
    expected_input_id:UUID
    idempotency_key:UUID

@app.post("/api/tasks/{task_id}/model-route")
async def switch_interview_model(task_id:str,body:TaskRouteSwitchRequest,a:auth.Actor=Depends(auth.require_actor)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    session=store.engine_session(task_id)
    if not session or task.get("simulated") or task.get("active_phase") not in ("intent","conops"):
        raise HTTPException(409,"only a live interview session can switch models")
    try:
        state=await run_in_threadpool(engine_client.snapshot,session)
        if state.task_id != task_id:raise HTTPException(409,"session belongs to another task")
        request=ModelRouteSwitchRequest(model_route=body.model_route,model=body.model,expected_sequence=body.expected_sequence,
            expected_input_id=body.expected_input_id,idempotency_key=body.idempotency_key,actor_id=a.label)
        changed=await run_in_threadpool(engine_client.switch_route,session,request)
    except EngineUnreachableError as exc:raise _engine_unreachable(exc)
    except EngineClientError as exc:raise HTTPException(exc.http_status,exc.error.model_dump(mode="json"))
    event=changed.accepted_event.route_change
    if event is None:raise HTTPException(502,"engine omitted route-change event")
    # A retried response must not duplicate the durable transcript event.
    existing=any(e.get("kind")=="model_route" and (e.get("payload") or {}).get("idempotency_key")==str(event.idempotency_key) for e in store.events(task_id))
    if not existing:
        old=f"{next(iter(event.from_model_map.values()))[0]}:{next(iter(event.from_model_map.values()))[1]}"
        new=f"{next(iter(event.to_model_map.values()))[0]}:{next(iter(event.to_model_map.values()))[1]}"
        store.add_event(task_id,"model_route",f"Model switched: {old} → {new}",phase_key=task.get("active_phase") or "intent",
            payload={"route":event.to_route,"resolved":new,"scope":"switch","actor":event.actor_id,
                "idempotency_key":str(event.idempotency_key),"route_version":event.route_version,
                "timestamp":event.timestamp.isoformat(),"from_model_map":event.from_model_map,"to_model_map":event.to_model_map,
                "sequence":event.session_sequence,"transcript":True})
    store.set_model_route(task_id,changed.snapshot.model_route or event.to_route)
    return changed.model_dump(mode="json")

class InterviewAnswer(BaseModel):
    answer:str=Field(min_length=1,max_length=8000)
    decision_id:str|None=Field(default=None,max_length=160)
@app.post("/api/tasks/{task_id}/answer")
async def answer_interview(task_id:str,body:InterviewAnswer,a:auth.Actor=Depends(auth.require_actor)):
    task_before=store.task(task_id) or {};input_before=runner.input_state(task_id)
    recent_answers=[{"event_id":event["id"],"phase_key":event.get("phase_key"),"decision_id":event.get("payload",{}).get("decision_id")} for event in store.events(task_id) if event.get("kind")=="answer"][-10:]
    activity("api.answer.attempt","Founder answer submission received",endpoint=f"/api/tasks/{task_id}/answer",task_id=task_id,task_state=task_before.get("state"),active_phase=task_before.get("active_phase"),submitted_decision_id=body.decision_id,open_decision_id=(input_before.get("decision") or {}).get("decision_id"),process_live=input_before["process_live"],waiting=input_before["waiting"],recent_answer_events=json.dumps(recent_answers,separators=(",",":")))
    try:
        session_id=store.engine_session(task_id)
        if not session_id:raise RuntimeError("task has no HTTP engine session")
        state=await run_in_threadpool(engine_client.snapshot,session_id);pending=state.open_input
        if pending is None:raise RuntimeError("task is not waiting for input")
        if body.decision_id and str(pending.input_id)!=body.decision_id:raise RuntimeError("submitted decision does not match live input")
        raw_value,separator,rationale=body.answer.partition(" - ")
        raw_value=raw_value.strip();rationale=rationale.strip() if separator else ""
        typed_value=next((option.value for option in pending.options if str(option.value)==raw_value),body.answer)
        if pending.rationale_required and not rationale:raise RuntimeError("typed input requires a rationale")
        submitted=await run_in_threadpool(engine_client.submit,session_id,InputResponse(input_id=pending.input_id,value=typed_value,
            rationale=rationale or None,actor_id=a.label))
        store.add_event(task_id,"engine_protocol","Typed founder input accepted",phase_key=task_before.get("active_phase") or "intent",payload=submitted.model_dump(mode="json"))
    except EngineUnreachableError as e:
        task=store.task(task_id) or {}
        payload={"endpoint":f"/api/tasks/{task_id}/answer",**_engine_unreachable_payload(e)}
        store.add_event(task_id,"error",f"Error: the interview engine is not reachable. Likely cause: nothing accepted the connection at {e.base_url} - the engine service stopped mid-interview. Next: restart scripts/run-ui.sh (it starts the engine automatically), or start the engine by hand: {ENGINE_START_HINT}. Your previous answers are checkpointed; the interview resumes from them. Then retry.",phase_key=task.get("active_phase"),payload=payload)
        activity("engine.unreachable","Interview engine unreachable while answering",task_id=task_id,phase=task.get("active_phase"),base_url=e.base_url,cause=str(e.cause))
        raise _engine_unreachable(e)
    except EngineClientError as e:
        task=store.task(task_id) or {}
        payload={"endpoint":f"/api/tasks/{task_id}/answer","http_status":e.http_status,"task_state":task.get("state"),"active_phase":task.get("active_phase"),"decision_id":body.decision_id,"protocol_error":e.error.model_dump(mode="json")}
        store.add_event(task_id,"error",f"Error: interview engine rejected the input. Likely cause: {e.error.detail} Next: retry this typed input if the protocol marks it retryable; otherwise refresh the live question.",phase_key=task.get("active_phase"),payload=payload)
        activity("engine.answer.rejected","Interview engine rejected founder input",task_id=task_id,phase=task.get("active_phase"),**payload)
        raise HTTPException(e.http_status,e.error.model_dump(mode="json"))
    except RuntimeError as e:
        task=store.task(task_id) or {};cause=str(e);next_step="Refresh the task and answer only the current typed input."
        payload={"endpoint":f"/api/tasks/{task_id}/answer","http_status":409,"task_state":task.get("state"),"active_phase":task.get("active_phase"),"decision_id":body.decision_id,"recent_answer_events":recent_answers,"reason":cause,"next":next_step}
        store.add_event(task_id,"error",f"Error: stale founder decision was not submitted. Likely cause: {cause} Next: {next_step}",phase_key=task.get("active_phase"),payload=payload)
        activity("api.answer.stale","Stale founder decision was not submitted",task_id=task_id,phase=task.get("active_phase"),**payload)
        raise HTTPException(409,{"error":"Stale founder decision was not submitted.","cause":cause,"next":next_step,"stale":True,"current_state":task.get("state"),"active_phase":task.get("active_phase")})
    active_phase=(store.task(task_id) or {}).get("active_phase") or "intent"
    store.add_event(task_id,"answer",f"You: {body.answer}",phase_key=active_phase,payload={"actor":a.label,"character_count":len(body.answer),"transcript":True,"decision_id":body.decision_id,"answer":body.answer})
    activity("engine.answer","Founder answer submitted",task_id=task_id,phase=active_phase,actor=a.label,character_count=len(body.answer))
    if _reconcile_terminal_http_interview(task_id,submitted.snapshot):
        asyncio.create_task(_continue_reconciled_interview(task_id))
    return {"status":"accepted"}

def _task_ui_state(task:dict,input_state:dict)->dict:
    """Single state doctrine for task-detail visibility and actionable input."""
    waiting_gate=next((gate for gate in task.get("gates",[]) if gate.get("state")=="waiting" and gate.get("after_phase")==task.get("active_phase")),None)
    live_decision=input_state.get("decision") if input_state.get("waiting") else None
    process_live=bool(input_state.get("process_live"))
    if task.get("state")=="not_started":mode="not_started"
    elif task.get("state")=="done":mode="complete"
    elif task.get("state")=="failed":mode="error"
    # A live subprocess blocked on stdin owns the next action. A durable
    # delivery gate may already be waiting for the same phase, but exposing it
    # here would send the founder's answer to the store instead of stdin.
    elif process_live and live_decision:mode="engine_decision"
    elif process_live and input_state.get("waiting"):mode="engine_question"
    elif waiting_gate:mode="phase_gate"
    elif process_live:mode="executing"
    else:mode="error"
    if mode not in UI_MODES:raise RuntimeError(f"unsupported UI mode: {mode}")
    diagnostic=None
    if mode=="error":
        cause=input_state.get("engine_error")
        diagnostic=({"reason":"engine protocol rejected the snapshot","http_status":cause["http_status"],"code":cause["code"],"detail":cause["detail"],"task_state":task.get("state"),"active_phase":task.get("active_phase")}
                    if cause else {"reason":"engine session completed" if input_state.get("engine_process_status")==int(ProcessStatus.COMPLETED) else "engine transport unavailable" if input_state.get("engine_unreachable") else "task has no live ActiveWork owner","task_state":task.get("state"),"active_phase":task.get("active_phase")})
    return {"mode":mode,"active_phase":task.get("active_phase"),"process_live":process_live,
            "decision":live_decision if mode=="engine_decision" else None,
            "question":input_state.get("question") if mode=="engine_question" else None,
            "input_id":input_state.get("input_id") if mode in ("engine_question","engine_decision") else None,
            "gate":waiting_gate if mode=="phase_gate" else None,"diagnostic":diagnostic}


def _task_artifact_snapshot(task_id:str)->list[dict]:
    """Project real session files into task artifacts; filesystem is source evidence."""
    task=store.task(task_id)
    if not task:return []
    session=_recorded_engine_session(task_id)
    if not session:return store.task_artifacts(task_id)
    from vmodel_contracts.runtime import artifacts_root
    root=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session
    from .phases import PHASE_DEFINITIONS
    records=[]
    validators={
        "intent_model.json":("vmodel_contracts.interview","IntentModel"),"conops.json":("vmodel_contracts.conops","ConOpsDocument"),
        "needs.json":("vmodel_contracts.needs","StakeholderNeedsSet"),"srs.json":("vmodel_contracts.srs","SRSDocument"),
        "architecture.json":("vmodel_contracts.architecture","ArchitectureOverview"),"items.json":("vmodel_contracts.spec","SpecBundle"),
        "spec_items.json":("vmodel_contracts.spec","SpecBundle"),"tests.json":("vmodel_contracts.verification","VerificationReport"),
        "test_specifications.json":("vmodel_contracts.verification","VerificationReport")}
    for phase in PHASE_DEFINITIONS:
        folder=root/("interview" if phase.key in ("intent","conops") else "spec")
        for filename in phase.artifacts:
            path=folder/filename
            if not path.is_file():continue
            valid=None;errors=[];validator=validators.get(filename)
            if validator:
                try:
                    model=getattr(__import__(validator[0],fromlist=[validator[1]]),validator[1]);model.model_validate_json(path.read_text());valid=True
                except Exception as exc:valid=False;errors=[f"{type(exc).__name__}: {str(exc)[:600]}"]
            records.append({"phase_key":phase.key,"name":filename,"title":_ARTIFACT_TITLES.get(filename,Path(filename).stem.replace("_"," ").title()),"ref":str(path),"mime_type":mimetypes.guess_type(path.name)[0] or "application/octet-stream","stale":False,"generation":task["generation"],"present":True,"valid":valid,"validator":validator[1] if validator else None,"validation_errors":errors,"validation_error":errors[0] if errors else None})
    return records or store.task_artifacts(task_id)

def _task_artifact(task_id:str,phase:str,name:str)->Path:
    artifact=next((a for a in _task_artifact_snapshot(task_id) if a["phase_key"]==phase and a["name"]==name),None)
    if not artifact:raise HTTPException(404,"artifact not found")
    path=Path(artifact["ref"]).resolve()
    if not path.is_file():raise HTTPException(404,"artifact file not found")
    return path

@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}")
def task_artifact_download(task_id:str,phase:str,name:str):
    return FileResponse(_task_artifact(task_id,phase,name),filename=name)

_ARTIFACT_TITLES={"intent_model.json":"Needs and Scope","conops.json":"Concept of Operations","needs.json":"Stakeholder Needs","srs.json":"Requirements","architecture.json":"Architecture","items.json":"Item Specifications","spec_items.json":"Item Specifications","tests.json":"Verification","test_specifications.json":"Verification"}

def _checkpoint_interview_turns(task_id:str)->list[tuple[str,str]]:
    """Recover historical turns when old API events stored only answer lengths."""
    session=_recorded_engine_session(task_id)
    if not session:return []
    from vmodel_contracts.runtime import artifacts_root
    db=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"/"checkpoints.sqlite"
    if not db.is_file():return []
    built=os.environ.get("REQUI_RE_DIST")=="1"
    engine_dir=(Path.cwd() if built else Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve())
    python=Path(os.environ.get("INTERVIEW_ENGINE_PYTHON",sys.executable if built else str(engine_dir/".venv/bin/python")))
    if not python.is_file():return []
    script="import json,sqlite3,sys\nfrom langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer\nfrom interview_engine.contracts import AppArchetype\nrow=sqlite3.connect(sys.argv[1]).execute('select type,checkpoint from checkpoints order by checkpoint_id desc limit 1').fetchone()\nif not row: print('[]');raise SystemExit\nobj=JsonPlusSerializer(allowed_msgpack_modules=[AppArchetype]).loads_typed((row[0],row[1]))\nmessages=(obj.get('channel_values') or {}).get('messages') or []\nout=[]\nfor m in messages:\n role=getattr(m,'type',None) or (m.get('type') if isinstance(m,dict) else None)\n content=getattr(m,'content',None) or (m.get('content') if isinstance(m,dict) else None)\n if role in ('ai','human') and isinstance(content,str):out.append([role,content])\nprint(json.dumps(out))"
    try:
        env={**os.environ,**({} if built else {"PYTHONPATH":str(engine_dir/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})}
        result=subprocess.run([str(python),"-c",script,str(db)],capture_output=True,text=True,timeout=8,env=env)
        if result.returncode:return []
        messages=json.loads(result.stdout);turns=[];question=None
        for role,content in messages:
            if role=="ai":question=content.replace("[INTERVIEW COMPLETE]","").strip()
            elif role=="human" and question:turns.append((question,content.strip()));question=None
        return turns
    except Exception:return []

def _interview_model_provenance(task_id:str)->list[str]:
    """Usage is per-call truth; never collapse a switched stage to its first model."""
    session=_recorded_engine_session(task_id)
    rows=[]
    if session:
        try:
            from vmodel_contracts.runtime import artifacts_root
            usage=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"/"usage.jsonl"
            if usage.is_file():
                rows=[json.loads(line) for line in usage.read_text().splitlines() if line.strip()]
        except (ValueError,OSError):pass
    segments=_model_usage_segments(rows)
    if segments:
        return [f"Model segment {x['route_version'] if x['route_version'] is not None else 'unassigned'}: {x['provider']}:{x['model']} ({x['calls']} calls)" for x in segments]
    resolved=next(((event.get("payload") or {}).get("resolved") for event in reversed(store.events(task_id)) if event.get("kind")=="model_route" and (event.get("payload") or {}).get("resolved")),None)
    return [f"Model: {resolved}"] if resolved else []

def _model_usage_segments(rows:list[dict])->list[dict]:
    segments=[]
    for row in rows:
        if not isinstance(row,dict) or not row.get("provider") or not row.get("model"):continue
        key=(row.get("route_version"),row["provider"],row["model"])
        if not segments or (segments[-1]["route_version"],segments[-1]["provider"],segments[-1]["model"])!=key:
            segments.append({"route_version":key[0],"provider":key[1],"model":key[2],
                "calls":0,"prompt_tokens":0,"completion_tokens":0,"cost_usd":0.0,"unpriced_calls":0})
        segment=segments[-1];segment["calls"]+=1
        prompt=int(row.get("prompt_tokens") or 0);completion=int(row.get("completion_tokens") or 0)
        segment["prompt_tokens"]+=prompt;segment["completion_tokens"]+=completion
        pin=row.get("price_per_mtok_prompt_usd");pout=row.get("price_per_mtok_completion_usd")
        if pin is None or pout is None:segment["unpriced_calls"]+=1
        else:segment["cost_usd"]+=(prompt*float(pin)+completion*float(pout))/1_000_000
    return segments

def _interview_transcript_markdown(task_id:str)->str:
    turns=[]
    for event in store.events(task_id):
        if event.get("phase_key") not in ("intent","conops"):continue
        payload=event.get("payload") or {}
        if event.get("kind") in ("waiting_input","founder_decision"):
            question=str(payload.get("question") or event.get("message") or "").strip()
            if question:turns.append([question,None])
        elif event.get("kind")=="answer":
            message=event.get("message","")
            answer=str(payload.get("answer") or (message.removeprefix("You: ") if message.startswith("You: ") else "")).strip()
            if answer and turns and turns[-1][1] is None:turns[-1][1]=answer
    complete=[tuple(turn) for turn in turns if turn[1]]
    checkpoint_turns=_checkpoint_interview_turns(task_id)
    if checkpoint_turns:complete=checkpoint_turns
    route_changes=[e for e in store.events(task_id) if e.get("kind")=="model_route" and (e.get("payload") or {}).get("scope")=="switch"]
    if not complete and not route_changes:return ""
    lines=_interview_model_provenance(task_id)+["","## Interview questions and answers"]
    # Keep switch markers in event order, between the questions and answers
    # they actually separate. Checkpoint-only legacy transcripts fall back to
    # the reconstructed complete turns.
    numbered=0
    if not checkpoint_turns:
        for event in store.events(task_id):
            if event.get("phase_key") not in ("intent","conops"):continue
            payload=event.get("payload") or {}
            if event.get("kind")=="model_route" and payload.get("scope")=="switch":
                lines.extend(["",f"**{event.get('message','Model switched')}** · {payload.get('timestamp','unknown time')} · segment {payload.get('route_version','?')} · actor {payload.get('actor','unknown')}"])
            elif event.get("kind") in ("waiting_input","founder_decision"):
                question=str(payload.get("question") or event.get("message") or "").strip()
                if question:
                    numbered+=1;lines.extend(["",f"### Question {numbered}",question])
            elif event.get("kind")=="answer":
                message=event.get("message","")
                answer=str(payload.get("answer") or (message.removeprefix("You: ") if message.startswith("You: ") else "")).strip()
                if answer:lines.extend(["",f"**Answer:** {answer}"])
    else:
        for index,(question,answer) in enumerate(complete,1):
            lines.extend(["",f"### Question {index}",question,"",f"**Answer:** {answer}"])
        for event in route_changes:
            payload=event.get("payload") or {}
            lines.extend(["",f"**{event.get('message','Model switched')}** · {payload.get('timestamp','unknown time')} · segment {payload.get('route_version','?')}"])
    return "\n".join(lines)


@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}/view")
def task_artifact_view(task_id:str,phase:str,name:str):
    path=_task_artifact(task_id,phase,name)
    if path.stat().st_size>2_000_000:raise HTTPException(413,"artifact is too large for inline viewing")
    text=path.read_text(errors="replace");raw=text;display_path=path
    if path.suffix==".json":
        sibling=path.with_suffix(".md")
        if sibling.is_file():display_path=sibling;text=sibling.read_text(errors="replace")
        try:raw=json.dumps(json.loads(raw),indent=2)
        except Exception:pass
    display_format="markdown" if display_path.suffix==".md" else ("json" if path.suffix==".json" else "text")
    transcript=_interview_transcript_markdown(task_id) if name=="intent_model.json" else ""
    if transcript:
        if display_format!="markdown":text=f"# {_ARTIFACT_TITLES[name]}\n\n"+text;display_format="markdown"
        text=text.rstrip()+"\n\n"+transcript+"\n"
    return {"name":name,"title":_ARTIFACT_TITLES.get(name,Path(name).stem.replace("_"," ").title()),"phase":phase,"format":display_format,"content":text,"raw_format":"json" if path.suffix==".json" else "text","raw_content":raw,"display_name":display_path.name}

@app.get("/api/tasks/{task_id}/artifacts.zip")
def task_artifacts_zip(task_id:str):
    records=_task_artifact_snapshot(task_id)
    if not records:raise HTTPException(404,"no artifacts found")
    data=io.BytesIO()
    with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as archive:
        for artifact in records:archive.write(artifact["ref"],f"{artifact['phase_key']}/{artifact['name']}")
    from fastapi.responses import Response
    title=(store.task(task_id) or {}).get("title","task").replace(" ","-").lower()
    return Response(data.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{title}-artifacts.zip"'})

_STRUCTURED_INPUT_KINDS=(InputKind.CHOICE,InputKind.APPROVAL)

def _sync_waiting_input_event(task_id:str,pending:InputRequest)->None:
    """Record each distinct engine input request once, so the question panel,
    the interview transcript and SSE state refreshes have durable evidence in
    HTTP mode (the subprocess runner used to emit these itself)."""
    input_id=str(pending.input_id)
    if any(event.get("kind")=="waiting_input" and str(event.get("payload",{}).get("input_id"))==input_id for event in store.events(task_id)):
        return
    phase=((store.task(task_id) or {}).get("active_phase")) or "intent"
    store.add_event(task_id,"waiting_input",pending.prompt,phase_key=phase,
        payload={"waiting_input":True,"question":pending.prompt,"input_id":input_id,"kind":int(pending.kind)})

def _http_input_state(task_id:str,engine_state:EngineSnapshot)->dict:
    """Project a live HTTP engine snapshot into the task input contract.

    Only structured inputs (choice/approval) may surface as decisions; a text
    question must fall through to the free-text answer composer. Mapping every
    open input to a decision leaves the view in engine_decision mode with no
    options, which renders no input control at all."""
    pending=engine_state.open_input
    waiting=engine_state.process_status==ProcessStatus.WAITING_INPUT and pending is not None
    decision=None;question=None;input_id=None
    if waiting:
        input_id=str(pending.input_id)
        if pending.kind in _STRUCTURED_INPUT_KINDS:decision=pending.model_dump(mode="json")
        else:question=pending.prompt
        _sync_waiting_input_event(task_id,pending)
    return {"waiting":waiting,"engine_process_status":int(engine_state.process_status),
            "process_live":engine_state.process_status in (ProcessStatus.RUNNING,ProcessStatus.WAITING_INPUT),
            "decision":decision,"requirement":None,"question":question,"input_id":input_id}

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, background_tasks: BackgroundTasks = None):
    task = store.task(task_id)
    if not task: raise HTTPException(404, "task not found")
    session_id=store.engine_session(task_id)
    if session_id:
        try:
            snapshot=engine_client.snapshot(session_id)
            if _reconcile_terminal_http_interview(task_id,snapshot):
                activity("engine.recovery.reconciled","Restored completed interview boundary from task read; queuing Stakeholder needs",task_id=task_id,session_id=session_id,sequence=snapshot.sequence,next_phase="needs")
                if background_tasks is not None:background_tasks.add_task(_continue_reconciled_interview,task_id)
                task=store.task(task_id)
            input_state=(_http_input_state(task_id,snapshot) if task.get("active_phase") in ("intent","conops")
                         else {**runner.input_state(task_id),"engine_process_status":int(snapshot.process_status)})
        except EngineClientError as exc:
            input_state={"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None,
                         "engine_error":{"http_status":exc.http_status,"code":int(exc.error.code),"detail":exc.error.detail}}
        except EngineUnreachableError:
            input_state={"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None,"engine_unreachable":True}
    else:input_state=runner.input_state(task_id)
    work=runner.work_state(task_id)
    work_state=({"owner":work.owner,"phase":work.phase,"status":work.status,"started_at":work.started_at,"durable_ref":work.durable_ref,"requirement":work.requirement} if work else None)
    artifacts=_task_artifact_snapshot(task_id);session=_recorded_engine_session(task_id)
    return {**task, "events": store.events(task_id), "artifacts":artifacts,"artifact_projection":{"session":session,"root":str((__import__('vmodel_contracts.runtime',fromlist=['artifacts_root']).artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR'))/session) if session else ''),"count":len(artifacts),"reason":"projected canonical session artifacts" if artifacts else ("session could not be recovered" if not session else "no configured artifacts found under session")}, "input_state":input_state,"active_work":work_state,"ui_state":_task_ui_state(task,input_state)}

@app.get("/api/tasks/{task_id}/execution")
def task_execution(task_id:str,debug:bool=False,a:auth.Actor=Depends(auth.require_actor)):
    """Per-session routing, usage and call diagnostics; raw payloads require debug mode."""
    if not store.task(task_id):raise HTTPException(404,"task not found")
    session=_recorded_engine_session(task_id)
    if not session:return {"session_id":None,"route":None,"usage":[],"calls":[],"raw":[]}
    from vmodel_contracts.runtime import artifacts_root
    root=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"
    def lines(path:Path,limit:int=300):
        if not path.is_file():return []
        with path.open(errors="replace") as stream:
            return [value for row in stream if (value:=_execution_json(row)) is not None][-limit:]
    pinned=None
    state=root/"http-session.json"
    if state.is_file():
        try:pinned=json.loads(state.read_text()).get("model_map")
        except (ValueError,OSError):pass
    route=(store.task(task_id) or {}).get("model_route")
    usage=lines(root/"usage.jsonl")
    calls=lines(root/"llm-calls.jsonl")
    summary=[]
    for entry in calls:
        item={key:entry.get(key) for key in ("ts","event","stage","provider","model","attempt","latency_s","usage") if key in entry}
        error=entry.get("error")
        if isinstance(error,dict):
            # The provider error envelope may contain a raw response. The
            # normal task view only needs its kind/status; full detail is debug.
            item["error"]=error if debug else {key:error[key] for key in ("kind","class","http_status","request_id") if key in error}
        summary.append(item)
    raw=[]
    if debug:
        raw.extend(lines(root/"debug-http.jsonl",100))
        debug_dir=root/"debug"
        if debug_dir.is_dir():
            for file in sorted(debug_dir.glob("*.txt"))[-30:]:
                if file.is_file() and file.stat().st_size<=1_000_000:
                    raw.append({"file":file.name,"content":file.read_text(errors="replace")})
    return {"session_id":session,"route":route,"effective_models":pinned,"usage":usage,"usage_segments":_model_usage_segments(usage),"route_history":[(e.get("payload") or {}) for e in store.events(task_id) if e.get("kind")=="model_route" and (e.get("payload") or {}).get("scope")=="switch"],"calls":summary,"raw":raw}

def _execution_json(row:str):
    try:return json.loads(row)
    except ValueError:return None

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

class FailedNeedsRetryRequest(BaseModel):
    confirm: bool = False

@app.post("/api/tasks/{task_id}/retry-needs")
async def retry_failed_needs(task_id:str,body:FailedNeedsRetryRequest,a:auth.Actor=Depends(auth.require_run_start)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    phases={phase["phase_key"]:phase["state"] for phase in task["phases"]}
    session=store.engine_session(task_id)
    eligible=(task["state"]=="failed" and task["active_phase"]=="needs" and phases["needs"]=="failed"
              and all(phases[key] in ("approved","done") for key in ("intent","conops"))
              and all(phases[key]=="pending" for key in ("srs","architecture","specs","tests"))
              and session and runner.work_state(task_id) is None and task_id not in runner.processes)
    if not eligible:raise HTTPException(409,"Only a failed Stakeholder needs phase with approved interview inputs and no live work can be retried")
    from vmodel_contracts.runtime import artifacts_root
    from vmodel_contracts.interview import IntentModel
    from vmodel_contracts.conops import ConOpsDocument
    interview=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"
    try:
        intent=IntentModel.model_validate_json((interview/"intent_model.json").read_text())
        conops=ConOpsDocument.model_validate_json((interview/"conops.json").read_text())
        if intent.session_id!=session or conops.session_id!=session:raise ValueError("interview artifact session IDs do not match")
    except (OSError,ValueError) as exc:
        raise HTTPException(409,f"The linked interview artifacts are missing or invalid: {exc}")
    from . import model_routes as downstream_routes
    prior_route=next(((event.get("payload") or {}).get("route") for event in reversed(store.events(task_id))
                      if event.get("kind")=="model_route" and event.get("phase_key")=="needs"),None)
    stored=prior_route or task.get("model_route") or "deepseek"
    route="deepseek" if stored.startswith("deepseek") else "ollama" if stored.startswith("ollama") else stored
    try:effective=downstream_routes.route_value(route)
    except ValueError:raise HTTPException(409,"The saved downstream model route is unsupported; fix it before retrying")
    latest_failure=next((event for event in reversed(store.events(task_id)) if event["phase_key"]=="needs" and event["kind"]=="error"),None)
    if not body.confirm:
        return {"status":"preview","phase":"needs","session_id":session,"retains":["intent","conops"],"model_route":effective,
                "last_failure":latest_failure["message"] if latest_failure else next(phase["detail"] for phase in task["phases"] if phase["phase_key"]=="needs"),
                "message":f"Retry Stakeholder needs from the approved interview using {effective}. This may make new model calls and incur cost. Prior failure and artifacts remain in the audit; the interview is not reopened."}
    if not store.claim_failed_needs_retry(task_id,a.label):raise HTTPException(409,"The task changed before the retry was confirmed; refresh and review it again")
    activity("phase.retry","Founder confirmed retry of Stakeholder needs",task_id=task_id,session_id=session,actor=a.label,model_route=effective)
    asyncio.create_task(_run_failed_needs_retry(task_id))
    return {"status":"started","phase":"needs","session_id":session}

async def _run_failed_needs_retry(task_id:str)->None:
    try:await orchestrator.execute_phase(task_id,"needs",mode=ExecutionMode.REDO)
    except Exception as exc:
        message=f"Stakeholder needs retry stopped: {type(exc).__name__}: {exc}"
        store.update_phase(task_id,"needs","failed",detail=message)
        store.add_event(task_id,"error",message,phase_key="needs",payload={"cause":str(exc),"retry":True})
        activity("phase.retry.failed","Stakeholder needs retry did not launch",task_id=task_id,error=message)

class RerunRequest(BaseModel):
    from_phase: str
    confirm: bool = False

@app.post("/api/tasks/{task_id}/rerun")
def rerun(task_id: str, req: RerunRequest, request: Request, a: auth.Actor = Depends(auth.require_run_start)):
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
    priced_by_phase={}
    for event in store.events(task_id):
        if event.get("kind")=="price":priced_by_phase[event.get("phase_key")]=event
    priced=list(priced_by_phase.values())
    totals={"tokens":sum(int((event.get("payload") or {}).get("tokens") or 0) for event in priced),"price":round(sum(float((event.get("payload") or {}).get("price") or 0) for event in priced),3),"phases_priced":len(priced)}
    return {"actor_mode": auth.actor(request).label, "entries": store.audit_entries(task_id),"totals":totals}

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
    model_route:Literal["ollama","deepseek"]|None=None
@app.post("/api/tasks/{task_id}/gates/{gate_key}/decide")
async def decide_gate(task_id:str,gate_key:str,req:GateDecision,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    try:
        if req.model_route=="deepseek" and not os.environ.get("DEEPSEEK_API_KEY"):raise ValueError("DeepSeek is not configured")
        gate=store.decide_gate(task_id,gate_key,req.action,selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,actor=a.label)
        phase=gate["after_phase"]
        if req.model_route:
            target=phase if req.action=="redo" else (__import__('delivery_api.phases',fromlist=['successor']).successor(phase).key if __import__('delivery_api.phases',fromlist=['successor']).successor(phase) else phase)
            store.add_event(task_id,"model_route",f"{target} override uses {req.model_route}",phase_key=target,payload={"route":req.model_route,"scope":"phase_override","actor":a.label})
        if req.action=="redo":asyncio.create_task(orchestrator.execute_phase(task_id,phase,mode=ExecutionMode.REDO,model_route=req.model_route))
        else:asyncio.create_task(orchestrator.continue_after(task_id,phase))
        return gate
    except KeyError:raise HTTPException(404,"gate not found")
    except ValueError as e:raise HTTPException(400,{"error":str(e),"cause":"The gate decision is incomplete.","next":"Choose, edit, or explain what the redo should change."})

class FeedbackWrite(BaseModel):
    input_ref: str = Field(min_length=1, max_length=64)
    target: Literal["question", "answer"]
    content: str = Field(default="", max_length=8000)
    thumb: Literal[1, -1] | None = None
    comment: str | None = Field(default=None, max_length=4000)

class FeedbackPatch(BaseModel):
    thumb: Literal[1, -1] | None = None
    comment: str | None = Field(default=None, max_length=4000)

@app.get("/api/tasks/{task_id}/feedback")
def list_feedback(task_id:str, a:auth.Actor=Depends(auth.require_actor)):
    if not store.task(task_id): raise HTTPException(404,"task not found")
    return {"feedback": store.list_feedback(task_id)}

@app.post("/api/tasks/{task_id}/feedback")
def create_feedback(task_id:str, req:FeedbackWrite, a:auth.Actor=Depends(auth.require_actor)):
    if not req.thumb and not (req.comment or "").strip():
        raise HTTPException(400,{"error":"Empty feedback.","cause":"Neither a thumb nor a comment was supplied.","next":"Add a thumb or a comment before saving."})
    try:
        return store.upsert_feedback(task_id,req.input_ref,req.target,req.content,req.thumb,(req.comment or "").strip() or None,a.label)
    except ValueError as e:
        raise HTTPException(400,str(e))

@app.patch("/api/tasks/{task_id}/feedback/{feedback_id}")
def patch_feedback(task_id:str, feedback_id:int, req:FeedbackPatch, a:auth.Actor=Depends(auth.require_actor)):
    try:
        return store.update_feedback(task_id,feedback_id,a.label,thumb=req.thumb,comment=(req.comment or "").strip() or None,admin=a.admin)
    except KeyError:
        raise HTTPException(404,"feedback not found")
    except PermissionError:
        raise HTTPException(403,"feedback belongs to another actor")
    except ValueError as e:
        raise HTTPException(400,str(e))

@app.delete("/api/tasks/{task_id}/feedback/{feedback_id}")
def remove_feedback(task_id:str, feedback_id:int, a:auth.Actor=Depends(auth.require_actor)):
    try:
        store.delete_feedback(task_id,feedback_id,a.label,admin=a.admin)
        return {"status":"deleted"}
    except KeyError:
        raise HTTPException(404,"feedback not found")
    except PermissionError:
        raise HTTPException(403,"feedback belongs to another actor")

def _qa_turns(events):
    """Pair interview questions with their answers, mirroring the web client's qaTurns."""
    turns=[]
    for e in events:
        if e.get("phase_key") not in ("intent","conops"): continue
        p=e.get("payload") or {}
        if e.get("kind") in ("waiting_input","founder_decision") or p.get("waiting_input"):
            q=str(p.get("question") or e.get("message") or "").strip()
            if q and not (turns and turns[-1]["a"] is None and turns[-1]["q"]==q):
                turns.append({"q":q,"a":None})
        elif e.get("kind")=="answer":
            m=str(e.get("message") or "");a=str(p.get("answer") or (m[5:] if m.startswith("You: ") else "")).strip()
            if a and turns and turns[-1]["a"] is None:
                turns[-1]["a"]=a
    return [t for t in turns if t["q"] and t["a"]]

def _content_ref(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]

@app.get("/api/tasks/{task_id}/classifications")
def task_classifications(task_id:str, a:auth.Actor=Depends(auth.require_actor)):
    """Per-answer shadow categorization (Jev vs baseline) for debug-mode chips."""
    if not store.task(task_id): raise HTTPException(404,"task not found")
    session=store.engine_session(task_id)
    out={"session":session,"answers":{}}
    if not session: return out
    from vmodel_contracts.runtime import artifacts_root
    root=artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR"))/session/"interview"/"categorization-shadow.jsonl"
    if not root.is_file():
        out["reason"]="no shadow file";return out
    turns=_qa_turns(store.events(task_id,limit=10000))
    try:
        records=[json.loads(line) for line in root.read_text().splitlines() if line.strip()]
    except Exception as e:
        out["reason"]=f"shadow file unreadable: {e}";return out
    for rec in records:
        slot=rec.get("slot")
        if slot not in ("intent","coverage"): continue
        state=str((rec.get("input") or {}).get("state") or "")
        if not state: continue
        for t in turns:
            if t["q"] in state and t["a"] in state:
                aref=_content_ref(t["q"]+"\n"+t["a"])
                entry=out["answers"].setdefault(aref,{})
                base,jev=rec.get("baseline") or {},rec.get("jev") or {}
                differed=(base.get("status")=="ok" and jev.get("status")=="ok" and base.get("verdict")!=jev.get("verdict"))
                entry[slot]={"jev":jev.get("verdict") or {},"baseline":base.get("verdict") or {},
                             "jev_status":jev.get("status"),"differed":differed,
                             "ts":rec.get("timestamp")}
    return out

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
