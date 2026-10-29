"""Thin job-control API plus the V-model web console, served on one port."""
from __future__ import annotations
from .diagnostic_evidence import spawn

import asyncio
import hashlib
from contextlib import asynccontextmanager
import json
import jwt
import signal
import subprocess
import threading
import logging
import time
import os
import re
import sys
import mimetypes
import zipfile
import io
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from vmodel_contracts import RunState, RunStatus

from . import config,local_route
from .execution_fence import control_plane_only
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from . import auth
from .jobs import JobQueue
from .runner import ProcessRunner
from .orchestrator import PhaseOrchestrator,ExecutionMode
from .engine_runtime import discover_engine_runtimes, save_python, saved_python
from .engine_client import HttpEngineClient,EngineClientError,EngineUnreachableError,QualityConfigRejected
from vmodel_contracts.interactive import InputKind,InputRequest,InputResponse,ProcessStatus,StartSessionRequest,EngineSnapshot,ModelRouteSwitchRequest
from .store import create_store
from .simulation import Simulation, ImportValidationError
from . import bundles
from . import model_routes as model_route_lib
from .observability import event as activity
from . import engine_log,server_log_buffer
server_log_buffer.install()
from . import diagnostic_evidence
diagnostic_evidence.install()

def _runtime_version() -> dict:
    # The full image has no MANIFEST.txt; the build script passes the four commit ids as REQUI_BUILD_ID.
    build_id=os.environ.get("REQUI_BUILD_ID","").strip()
    if build_id and build_id!="unknown":
        # The build script also passes name=commit pairs, so the repositories are named (commit ids only, nothing else).
        repos={}
        for pair in os.environ.get("REQUI_BUILD_REPOS","").split(","):
            name,_,sha=pair.partition("=")
            if re.fullmatch(r"[a-z0-9-]{1,40}",name) and re.fullmatch(r"[0-9a-f]{7,40}",sha):repos[name]=sha
        # Image build timestamp (UTC ISO-8601) baked in by the build script; absent for old
        # images - the key is omitted rather than null so the runtime dict keeps one shape
        # per source (pinned by test_the_server_names_the_four_repositories_of_the_running_image).
        built_at=os.environ.get("REQUI_BUILD_AT","").strip() or None
        out={"runtime_id":build_id,"repositories":repos,"manifest":None}
        if built_at:out["built_at"]=built_at
        return out
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
store = (__import__('delivery_api.store',fromlist=['TursoReplicaStore']).TursoReplicaStore(str(config.local_database_path()),config.TURSO_DATABASE_URL,config.TURSO_AUTH_TOKEN,config.TURSO_SYNC_INTERVAL) if config.TURSO_DATABASE_URL else create_store(config.DATABASE_URL))
jobs = JobQueue(store)
runner = ProcessRunner(store)
orchestrator = PhaseOrchestrator(store,runner)
runner.on_complete = orchestrator.continue_after
simulation = Simulation(store)
engine_client = HttpEngineClient(config.INTERVIEW_ENGINE_URL,token=config.INTERVIEW_ENGINE_TOKEN)
def _set_engine_client(client):
    global engine_client
    engine_client=client

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
_previous_shutdown_handlers={}
_streams_closed_on_signal=0

_SESSION_UNSET=object()

def _recorded_engine_session(task_id:str,recorded=_SESSION_UNSET,events=None)->str|None:
    # The typed HTTP cutover stores this binding at session creation. It is
    # authoritative; event/path scanning is legacy recovery only. A request
    # that already read the binding (and the events) passes them in: one
    # statement per request, not two.
    if recorded is _SESSION_UNSET:recorded=store.engine_session(task_id)
    if recorded:return recorded
    if config.CLOUD_TENANTS:return None
    for event in reversed(store.events(task_id) if events is None else events):
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
        spawn(recover())


async def _drain_engine_progress(task_id:str,session_id:str,*,allow_absent:bool=False)->None:
    """Read all committed journal pages; independent of the engine control lock."""
    from .progress_adapter import ingest_interview_page
    while True:
        cursor=store.progress_cursor(task_id,"interview",session_id)
        fetched_at=time.monotonic()
        try:page=await run_in_threadpool(engine_client.progress,session_id,cursor)
        except EngineClientError as exc:
            if allow_absent and exc.http_status==404:return
            raise
        fetch_ms=int((time.monotonic()-fetched_at)*1000)
        ingest_at=time.monotonic()
        ingest_interview_page(store,task_id,session_id,page)
        if page.events:
            from datetime import datetime,timezone
            now=datetime.now(timezone.utc)
            lags=[max(0,int((now-event.timestamp).total_seconds()*1000)) for event in page.events]
            activity('progress.ingest.timing','Engine progress page retained',task_id=task_id,
                     session_id=session_id,events=len(page.events),cursor=cursor,next_cursor=page.next_cursor,
                     fetch_ms=fetch_ms,ingest_ms=int((time.monotonic()-ingest_at)*1000),
                     source_age_max_ms=max(lags),source_age_min_ms=min(lags))
        if not page.events:return
        if page.next_cursor<=cursor:raise RuntimeError("engine progress page made no cursor advance")

async def _watch_engine_advance(task_id:str,session_id:str,stop:asyncio.Event)->None:
    """Fetch progress during a blocking Start/Submit and drain after it returns."""
    while not stop.is_set():
        try:await _drain_engine_progress(task_id,session_id,allow_absent=True)
        except (EngineClientError,EngineUnreachableError,ValueError,RuntimeError,AttributeError) as exc:
            activity("progress.ingest.failed","Engine progress read failed without affecting control",task_id=task_id,session_id=session_id,error=str(exc)[:300])
        try:await asyncio.wait_for(stop.wait(),1)
        except asyncio.TimeoutError:pass
    try:await _drain_engine_progress(task_id,session_id,allow_absent=True)
    except (EngineClientError,EngineUnreachableError,ValueError,RuntimeError,AttributeError) as exc:
        activity("progress.drain.failed","Final engine progress drain failed",task_id=task_id,session_id=session_id,error=str(exc)[:300])

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
    interview=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),state.session_id,store)/"interview"
    try:
        intent=IntentModel.model_validate_json(_read_task_text(task_id,interview/"intent_model.json"))
        conops=ConOpsDocument.model_validate_json(_read_task_text(task_id,interview/"conops.json"))
        if intent.session_id!=state.session_id or conops.session_id!=state.session_id:
            raise ValueError("interview artifacts belong to a different session")
    except (OSError,ValueError) as exc:
        activity("engine.recovery.artifacts_invalid","Completed interview cannot be handed off without verified artifacts",task_id=task_id,session_id=state.session_id,error=str(exc)[:350])
        return False
    return store.reconcile_completed_interview(task_id,state.session_id,state.sequence)

def _customer_denial(exc,task_id=None):
    """The plain message for a customer; the raw reason stays in the server log and the activity stream."""
    from .user_messages import user_denial_message
    activity("account.denial.internal","A safety check denied a request",task_id=task_id,reason=str(exc)[:300])
    return user_denial_message(str(exc))

def _finish_cloud_interview(task_id:str,snapshot)->None:
    """Record the finished interview in cloud without starting the next phase.

    The next phase spends the owner's credits and needs its own spend approval,
    which the interview-only admission does not cover. A failure here is shown
    as an event; the engine already accepted the answer, so /answer must not 500."""
    try:done=_reconcile_terminal_http_interview(task_id,snapshot)
    except Exception as exc:
        store.add_event(task_id,"error","Interview finished, but recording it failed.",phase_key="conops",
                        payload={"cause":f"{type(exc).__name__}: {str(exc)[:300]}","session_id":str(snapshot.session_id)})
        activity("engine.cloud_finish.failed","Cloud interview completion could not be recorded",task_id=task_id,error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    if done:
        store.add_event(task_id,"progress","Interview complete. The next phase is ready. It starts only when you press Start, and it uses your credits.",phase_key="conops",
                        payload={"session_id":str(snapshot.session_id),"next_phase":"needs","started":False})

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
    from .recovery import plan_recovery,flag_orphaned_running
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
                if exc.http_status==404:
                    flag_orphaned_running(store,task_id,evidence='engine_session_not_found')
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
                # The architecture baseline choice is a local future, not an HTTP
                # input. Restore that owner after restart without replaying an
                # interview or launching a generator. A written baseline means
                # the old decision must not be reopened.
                if (current["state"]=="running" and current["active_phase"]=="architecture"
                    and phases.get("architecture")=="running" and phases.get("srs") in ("approved","done")
                    and not orchestrator._baseline_exists(task_id)):
                    latest_choice=next((event for event in reversed(events) if event.get("phase_key")=="architecture"
                                        and event.get("kind") in ("founder_decision","answer","baseline","requirement","command")),None)
                    if latest_choice and latest_choice["kind"]=="founder_decision" and (latest_choice.get("payload") or {}).get("decision",{}).get("decision_id")=="system-baseline":
                        activity("architecture.recovery.decision","Continuing interrupted baseline choice as greenfield",task_id=task_id,session_id=session)
                        spawn(orchestrator.execute_phase(task_id,"architecture"))
                        continue
                if repaired or (phases["conops"] in ("approved","done") and phases["needs"] in ("pending","running")
                                and current["active_phase"]=="conops"
                                and any(event["kind"]=="progress" and (event.get("payload") or {}).get("source")=="terminal_engine_snapshot"
                                        and (event.get("payload") or {}).get("session_id")==session for event in events)):
                    if not repaired:activity("engine.recovery.handoff_retry","Retrying reconciled interview handoff",task_id=task_id,session_id=session,next_phase="needs")
                    spawn(_continue_reconciled_interview(task_id))
                else:
                    now=store.task(task_id)
                    phase_states={phase["phase_key"]:phase["state"] for phase in now["phases"]}
                    path=authorized_session(__import__('vmodel_contracts.runtime',fromlist=['artifacts_root']).artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"
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
            spawn(launch())
        elif plan.action=="restore_gate":
            gate=store.wait_at_gate(task_id,plan.phase or task.get("active_phase"))
            activity("gate.owner.restored","Restored founder approval gate",task_id=task_id,phase=plan.phase,gate_key=(gate or {}).get("gate_key"))
        elif plan.action=="restore_wait":
            requirement_event=next((event for event in reversed(events) if event.get("kind")=="requirement" and (event.get("payload") or {}).get("kind")=="external_input"),{})
            requirement=(requirement_event.get("payload") or {})
            runner.pause_for_requirement(task_id,plan.phase or task.get("active_phase") or "architecture",requirement)
        elif plan.action=="resume" and session:
            await _resume_one_interview(task_id,session)
        elif plan.action=='idle' and not session:
            flag_orphaned_running(store,task_id,evidence='no_session_or_worker')

async def _catch_up_interview_progress()->None:
    """Resume journal ingestion for linked sessions after delivery restarts."""
    for task in store.list_tasks():
        session=store.engine_session(task["task_id"])
        if not session:continue
        if task.get("state")=="running" and task.get("active_phase")=="intent":
            events=store.events(task["task_id"])
            pending=any(e.get("kind")=="command" and e.get("message")=="Interview starting" and
                        (e.get("payload") or {}).get("session_id")==session for e in events)
            completed=any(e.get("kind")=="engine_protocol" and
                          (e.get("payload") or {}).get("snapshot",{}).get("session_id")==session for e in events)
            if pending and not completed:
                try:
                    state=await run_in_threadpool(engine_client.snapshot,session)
                    if state.session_id!=session or state.task_id!=task["task_id"]:raise ValueError("session identity mismatch")
                    store.add_event(task["task_id"],"engine_protocol","Recovered interview Start response",phase_key="intent",payload={"snapshot":state.model_dump(mode="json")})
                    store.update_phase(task["task_id"],"intent","running",detail="Waiting for founder input" if state.open_input else "Interview engine running")
                except Exception as exc:
                    store.add_event(task["task_id"],"error","Interrupted interview Start needs review",phase_key="intent",payload={"session_id":session,"cause":f"{type(exc).__name__}: {str(exc)[:200]}","next":"Check the engine journal and checkpoint. Do not start another session until the old one is classified."})
                    store.update_phase(task["task_id"],"intent","failed",detail="Interrupted interview Start needs review")
                    continue
        try:await _drain_engine_progress(task["task_id"],session,allow_absent=True)
        except (EngineClientError,EngineUnreachableError,ValueError,RuntimeError,AttributeError) as exc:
            activity("progress.recovery.failed","Could not catch up interview progress",task_id=task["task_id"],session_id=session,error=str(exc)[:250])

async def _resume_one_interview(task_id:str,session:str)->None:
    store.add_event(task_id,"incompatible","Pre-HTTP interview requires restart",phase_key="intent",payload={"process_status":int(ProcessStatus.INCOMPATIBLE),"next_step":60,"legacy_session":session})


@asynccontextmanager
async def lifespan(application:FastAPI):
    global _streams_closed_on_signal
    _streams_closed_on_signal=0
    config.validate_auth_config()
    from . import cloud_execution
    if cloud_execution.admitted():
        # Open: start the in-process engines under the money gateway. A failure keeps execution closed (cloud_execution reports it).
        from . import cloud_runtime
        try:cloud_runtime.install(store,os.environ,_set_engine_client,watch_vendor=True)
        except Exception as exc:print('Cloud execution: could not start the in-process engine: '+f'{type(exc).__name__}: {str(exc)[:300]}',file=sys.stderr)
    cloud_execution.log_status()
    print("Build: "+(", ".join(f"{k} {v}" for k,v in RUNTIME_VERSION["repositories"].items()) or RUNTIME_VERSION["runtime_id"]),file=sys.stderr)
    from . import run_metrics
    run_metrics.start_idle_sampler()     # CPU and memory are read on a timer, so the operator page has numbers when no run is active
    loop_lag_task=spawn(_loop_lag_monitor())
    from .observability import LOG_PATH,stage_log_paths
    print(f"Server log: {LOG_PATH.resolve()}",file=sys.stderr)
    for phase,path in stage_log_paths().items():print(f"Stage log [{phase}]: {path.resolve()}",file=sys.stderr)
    if not config.CLOUD_TENANTS and not control_plane_only():
        simulation.reconcile_interrupted();await jobs.start();await _recover_tasks();await _catch_up_interview_progress()
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT,signal.SIGTERM):
            if signal.getsignal(signum) is not _shutdown_signal:
                _previous_shutdown_handlers[signum]=signal.getsignal(signum)
                signal.signal(signum,_shutdown_signal)
    try:yield
    finally:
        loop_lag_task.cancel()
        stream_count=_streams_closed_on_signal+len(_stream_shutdown_events)
        activity("server.shutdown.signal","Server shutdown received",streams=stream_count,engines=len(runner.processes))
        for event in tuple(_stream_shutdown_events):event.set()
        await runner.shutdown();await jobs.stop()
        run_metrics.stop_idle_sampler()
        from . import cloud_runtime
        cloud_runtime.uninstall()
        activity("server.shutdown.complete","Server shutdown complete",streams_closed=stream_count)
        if threading.current_thread() is threading.main_thread():
            for signum,previous in _previous_shutdown_handlers.items():
                if signal.getsignal(signum) is _shutdown_signal:signal.signal(signum,previous)
            _previous_shutdown_handlers.clear()

def _shutdown_signal(signum,frame):
    global _streams_closed_on_signal
    count=len(_stream_shutdown_events);_streams_closed_on_signal+=count
    activity("server.shutdown.signal","Server shutdown signal received",streams=count,signal=signal.Signals(signum).name)
    for event in tuple(_stream_shutdown_events):event.set()
    previous=_previous_shutdown_handlers.get(signum)
    if callable(previous):previous(signum,frame)


app = FastAPI(title="Requi.re API", version="0.2.0", lifespan=lifespan)
app.state.store = store
if config.CLOUD_TENANTS:
    from .supabase_auth import SupabaseIdentityVerifier
    from .tenant_http import TenantMiddleware
    app.add_middleware(TenantMiddleware,verifier=SupabaseIdentityVerifier(config.SUPABASE_URL),
                       admin_subjects=config.SUPABASE_ADMIN_SUBJECTS,session_check=store.auth_session_active,
                       request_database=getattr(store,'_postgres',None))
from .tenant_files import authorized_session,session_reader,read_text as read_bound_text
from .tenancy import TenantAccessDenied
from .postgres_scope import RequestStats, request_stats

@app.exception_handler(Exception)
async def unexpected_error(request,exc):
    """Any unhandled error: the browser gets the reference and the error's type (never its message, which may carry connection details);
    the traceback is already in the server log under the same reference. The exception still propagates to the server and to tests."""
    ref=getattr(request.state,'ref',None) or uuid4().hex[:8]
    return JSONResponse({'detail':{'error':'The server hit an unexpected error.','cause':'Reference %s, %s.'%(ref,type(exc).__name__),'next':'Retry once. If it repeats, open the avatar Debug dialog and send the reference.'}},status_code=500,headers={'X-Request-Id':ref,'Cache-Control':'no-store'})

@app.exception_handler(TenantAccessDenied)
async def tenant_access_denied(request,exc):
    return JSONResponse({'detail':'Tenant resource not found'},status_code=404)


def _session_bundle(session_id):
    if config.CLOUD_TENANTS:
        with session_reader(bundles.root(),session_id,store) as root:
            result=bundles.bundle(session_id,base=root)
        result["session"].pop("path",None)
        return _customer_events(result)
    return bundles.bundle(session_id)


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

# Only these four POSTs talk to Supabase Auth; they write nothing to the app database.
# Allowed only with a cloud (PostgreSQL) profile; the local SQLite auth path stays fenced.
CONTROL_PLANE_AUTH_ROUTES=frozenset({"/auth/signup","/auth/signin","/auth/refresh","/auth/logout"})

SLOW_REQUEST_MS=1000  # a request slower than this keeps its line on the console (with its DB cost)

def _log_request(method,path,status,duration_ms):
    # Successful health/static/telemetry and polling reads are not incident
    # evidence. Failures and slow requests remain, regardless of endpoint.
    if status>=400 or duration_ms>=SLOW_REQUEST_MS:return True
    return not (path in ('/health','/favicon.ico','/api/client-log') or path.startswith('/assets/') or
                (method=='GET' and (path in ('/api/auth','/api/account/credits') or
                  re.fullmatch(r'/api/tasks/[^/]+(?:/(?:stream|diagnostics|execution|phase-output|feedback))?',path))))

async def _request_log(request, call_next):
    if control_plane_only() and request.method not in ('GET','HEAD','OPTIONS') and not (config.CLOUD_TENANTS and request.method=='POST' and request.url.path in CONTROL_PLANE_AUTH_ROUTES):
        return JSONResponse({'detail':'Delivery-only runtime: mutations and execution remain fenced'},status_code=503,headers={'Cache-Control':'no-store'})
    if request.url.path.startswith('/api/operator/') and not config.CLOUD_TENANTS:
        return JSONResponse({'detail':'Operator resource not found'},status_code=404,headers={'Cache-Control':'no-store'})
    try:
        auth.csrf_guard(request)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    started=request.state.diagnostic_started
    ref=request.state.ref
    stats=request_stats.get()
    try:response=await call_next(request)
    except Exception:
        log.exception("request failed ref=%s method=%s path=%s",ref,request.method,request.url.path)
        raise
    who = auth.actor(request).label
    duration_ms=int((time.monotonic()-started)*1000)
    from . import request_costs
    request_costs.record_request(request.method,request.url.path,response.status_code,duration_ms,stats)
    response.headers["X-Request-Id"]=ref;response.headers["Server-Timing"]="app;dur=%d"%duration_ms+(", "+stats.header() if (stats.queries or stats.engine_calls or stats.checkout_seconds or stats.auth_seconds) else "")
    import logging as _logging
    _noteworthy=response.status_code>=400 or duration_ms>=SLOW_REQUEST_MS
    # Routine successes go to the file log only (still in Debug evidence/ZIP);
    # the console keeps non-2xx, slow and error lines. The per-request DB cost
    # rides every line so a slow request carries its own evidence.
    if _log_request(request.method,request.url.path,response.status_code,duration_ms):activity("http.request","Request completed",level=_logging.WARNING if _noteworthy else _logging.INFO,console=_noteworthy,ref=ref,method=request.method,path=request.url.path,status=response.status_code,duration_ms=duration_ms,db_queries=stats.queries,db_ms=int(stats.db_seconds*1000),engine_calls=stats.engine_calls,engine_ms=int(stats.engine_seconds*1000),checkout_ms=int(stats.checkout_seconds*1000),auth_ms=int(stats.auth_seconds*1000),held_ms=int(stats.tx_held_seconds*1000),actor=who,client=request.client.host if request.client else "unknown")
    if response.status_code >= 400:
        activity("http.error","API request returned an error",ref=ref,endpoint=request.url.path,method=request.method,status=response.status_code,duration_ms=duration_ms,actor=who,client=request.client.host if request.client else "unknown")
    if request.url.path.startswith("/api/operator/"):
        response.headers["Cache-Control"]="no-store"
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"]="no-cache"    # keep a copy but ask the server each time; an unchanged file answers 304 with no body
    elif request.url.path in ("/","/credits") or request.url.path.startswith("/tasks/"):
        response.headers["Cache-Control"]="no-store, max-age=0"
    if response.status_code in (401,403):activity("access.blocked","Unauthorized or forbidden request",method=request.method,path=request.url.path,status=response.status_code,actor=who)
    return response

@app.middleware("http")
async def request_log(request,call_next):
    ref=uuid4().hex[:8]
    request.state.ref=ref
    request.state.diagnostic_started=time.monotonic()
    stats_token=request_stats.set(RequestStats())
    match=re.fullmatch(r'/api/tasks/([^/]+)(?:/.*)?',request.url.path)
    token=diagnostic_evidence.context.set({'request_id':ref,**({'task_id':match.group(1)} if match else {})})
    try:
        response=await _request_log(request,call_next)
        if 'X-Request-Id' not in response.headers:
            activity('http.refused','Request refused before endpoint',level=logging.WARNING,method=request.method,path=request.url.path,status=response.status_code)
            response.headers['X-Request-Id']=ref
        return response
    finally:
        request_stats.reset(stats_token)
        diagnostic_evidence.context.reset(token)

def _page(name,headers=None):
    """Every HTML page is the app page: the persistent SPA shell (web/spa-shell.js) provides the one header at runtime, so no page carries its own copy. The boot config is injected at <!--REQUI_CONFIG--> so the preview row has its space from the first paint."""
    from fastapi.responses import HTMLResponse
    text=(WEB_DIR/name).read_text(encoding="utf-8")
    import json as _json
    boot=_json.dumps({"preview":os.environ.get("DELIVERY_PREVIEW")=="1","local_preview":os.environ.get("DELIVERY_LOCAL_PREVIEW")=="1","execution_enabled":os.environ.get("DELIVERY_CLOUD_EXECUTION")=="1"})
    text=text.replace("<!--REQUI_CONFIG-->","<script>window.__REQUI_CONFIG__="+boot+";</script>")
    return HTMLResponse(text,headers=headers or {"Cache-Control":"no-cache"})

@app.get("/", include_in_schema=False)
def index(): return _page("index.html")

@app.get("/api/auth")
def auth_status(request: Request):
    a = auth.actor(request)
    mode="supabase" if config.CLOUD_TENANTS else "basic" if config.BASIC_AUTH_ENABLED else ("oauth" if auth.enabled() else "local")
    session=request.app.state.store.session(request.cookies[config.SESSION_COOKIE]) if not config.CLOUD_TENANTS and auth.enabled() and request.cookies.get(config.SESSION_COOKIE) and a.id!="anonymous" else None
    result={"mode":mode,"authenticated":a.id not in {"anonymous"},"actor":a.email or a.label,"admin":a.admin,
            "providers":[p for p,ident in (("google",config.GOOGLE_CLIENT_ID),("github",config.GITHUB_CLIENT_ID)) if ident],"workspace":"private" if config.CLOUD_TENANTS else "shared"}
    if os.environ.get("DELIVERY_PREVIEW")=="1":result["preview"]=True
    if os.environ.get("DELIVERY_LOCAL_PREVIEW")=="1":result["local_preview"]=True
    if config.CLOUD_TENANTS and a.id!='anonymous':
        from .supabase_sessions import ACCESS_COOKIE
        token=request.cookies.get(ACCESS_COOKIE)
        if token:
            result['expires_at']=jwt.decode(token,options={'verify_signature':False}).get('exp')
    if session:result["expires_at"]=session["expires_at"]
    return result

class ClientLog(BaseModel):
    kind:str=Field(max_length=80)
    message:str=Field(max_length=16000)
    path:str=Field(max_length=1000)
    detail:dict={}

@app.post("/api/client-log",status_code=204)
def client_log(request:Request,body:ClientLog):
    # The browser's own detail keys are namespaced: a key such as "actor" or "path" must neither crash the call nor overwrite what the server records.
    activity("client."+body.kind,body.message,path=body.path,actor=auth.actor(request).label,client=request.client.host if request.client else "unknown",**{"detail_"+str(key):value for key,value in body.detail.items()})

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
    if config.CLOUD_TENANTS:
        from .supabase_sessions import signup as cloud_signup
        return cloud_signup(body.username,body.password)
    activity("auth.signup.attempt","Signup attempted",username=body.username,client=request.client.host if request.client else "unknown")
    try:result=auth.basic_signup(request,body.username,body.password);activity("auth.signup.success","Account created",username=body.username);return _auth_response(result)
    except HTTPException as e:activity("auth.signup.failure","Signup rejected",username=body.username,status=e.status_code,reason=(e.detail.get("error") if isinstance(e.detail,dict) else e.detail));raise

@app.post("/auth/signin")
def signin(request:Request,body:BasicCredentials):
    if config.CLOUD_TENANTS:
        from .supabase_sessions import signin as cloud_signin
        return cloud_signin(body.username,body.password)
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

@app.post("/auth/refresh")
def refresh_auth(request: Request):
    if config.CLOUD_TENANTS:
        from .supabase_sessions import refresh
        return refresh(request)
    sid,expires_at = auth.refresh_session(request)
    response = JSONResponse({"status": "refreshed", "expires_at": expires_at})
    response.set_cookie(config.SESSION_COOKIE, sid, max_age=config.SESSION_TTL_SECONDS,
                        httponly=True, secure=config.COOKIE_SECURE, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/auth/logout")
def logout(request: Request):
    if config.CLOUD_TENANTS:
        from .supabase_sessions import logout as cloud_logout
        return cloud_logout(request)
    current=auth.actor(request);activity("auth.signout","Sign-out requested",actor=current.label)
    sid=request.cookies.get(config.SESSION_COOKIE)
    if sid: store.delete_session(sid)
    from fastapi.responses import JSONResponse
    response=JSONResponse({"status":"signed_out"}); response.delete_cookie(config.SESSION_COOKIE,path="/"); return response

@app.get("/credits",include_in_schema=False)
def credits_page():return _page("index.html")

@app.get("/ops",include_in_schema=False)
def operator_page():
    # The operator views mount in the app shell. Every number on them comes from an admin-only endpoint, so a visitor who is not an operator sees only the refusal.
    return _page("index.html",{"Cache-Control":"no-store"})

@app.get("/about",include_in_schema=False)
def about_page():return _page("index.html")

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return FileResponse(WEB_DIR / "favicon.ico", media_type="image/x-icon")

@app.get("/tasks/{task_id}/gates/{gate_key}", include_in_schema=False)
def gate_page(task_id: str, gate_key: str): return _page("index.html")

@app.get("/tasks/{task_id}/complete", include_in_schema=False)
def complete_page(task_id: str): return _page("index.html")

@app.get("/tasks/{task_id}/decisions/{wait_id}", include_in_schema=False)
def decision_page(task_id:str,wait_id:str):return _page("index.html")

@app.get("/tasks/{task_id}/view", include_in_schema=False)
def task_view(task_id: str): return _page("index.html")

# The old session-bundle pages are gone; the Home page lists the person's own tasks. Old links and bookmarks land on Home.
@app.get("/sessions", include_in_schema=False)
@app.get("/sessions/{session_id}", include_in_schema=False)
@app.get("/sessions/{session_id}/view", include_in_schema=False)
@app.get("/sessions/{session_id}/{phase}", include_in_schema=False)
def old_session_pages_go_home(session_id: str = "", phase: str = ""):
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/", status_code=302)

@app.get("/api/sessions")
def sessions():
    if config.CLOUD_TENANTS:
        result=[]
        for task in store.list_tasks():
            session=store.engine_session(task['task_id'])
            if session:
                base=authorized_session(bundles.root(),session,store)
                with session_reader(bundles.root(),session,store):row=bundles.session_summary(base)
                if row:
                    row.pop('path',None);result.append(row)
        return {"sessions":list({row['session_id']:row for row in result}.values())}
    return {"sessions":bundles.discover(),"artifacts_root":str(bundles.root())}

@app.get("/api/settings/engine-python")
async def engine_python_settings(a:auth.Actor=Depends(auth.require_admin)):
    engine_dir=Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve()
    candidates=await discover_engine_runtimes(engine_dir)
    return {"path":str(saved_python() or ""),"candidates":[str(x) for x in candidates],"count":len(candidates)}
class EnginePythonRequest(BaseModel):path:str
@app.post("/api/settings/engine-python")
async def set_engine_python(req:EnginePythonRequest,a:auth.Actor=Depends(auth.require_admin)):
    engine_dir=Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve()
    try:path=await save_python(req.path,engine_dir)
    except ValueError as e:raise HTTPException(400,{"error":"Engine Python could not be saved.","cause":str(e),"next":"Choose one of the detected Python environments that passes the dependency check."})
    activity("engine.settings","Engine Python saved",actor=a.label,python=str(path));return {"path":str(path),"status":"saved"}

class ArtifactsRootRequest(BaseModel): path: str
@app.post("/api/settings/artifacts-root")
def set_artifacts_root(req: ArtifactsRootRequest, a: auth.Actor = Depends(auth.require_admin)):
    try:out=bundles.save_root(req.path)
    except ValueError as e:
        activity("settings.save_refused","Artifacts folder save refused",actor=a.label,key="artifacts-root",reason=str(e)[:200])
        raise HTTPException(400,{"error":"Artifacts folder could not be loaded.","cause":str(e),"next":"Choose the folder that directly contains your session directories, then validate again."})
    activity("settings.saved","Artifacts folder saved",actor=a.label,key="artifacts-root")
    return out

@app.get("/api/sessions/{session_id}")
def session_bundle(session_id: str):
    try:return _session_bundle(session_id)
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
        raise HTTPException(402, f"run stopped by budget guard at ${total:.2f}; mint a new link to continue")
    return {"run_total_usd": total}

@app.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str): return store.artifacts(run_id)

# Task-oriented UI API.
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=4000)

@app.get("/api/tasks")
def list_tasks(): return _customer_events({"tasks": store.list_tasks()})

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

@app.post("/api/tasks/{task_id}/stop")
def stop_task(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    # Local subprocess Stop isn't the approved cooperative protocol. Cloud gate
    # remains closed until provider/lifecycle chain is fully installed.
    if not config.CLOUD_TENANTS:raise HTTPException(404,"cooperative Stop unavailable")
    if not store.task(task_id):raise HTTPException(404,"task not found")
    if not cloud_execution_admitted():raise HTTPException(503,"Cloud admission and lifecycle validation pending")
    from .cooperative_stop import request_stop
    from .attempt_ledger import AdmissionDenied
    activity("task.stop.requested","Cooperative stop requested",task_id=task_id,actor=a.label)
    try:out=request_stop(store,engine_client,task_id)
    except KeyError:raise HTTPException(404,"task not found")
    except AdmissionDenied as exc:
        activity("task.stop.refused","Cooperative stop refused",task_id=task_id,actor=a.label,reason="admission")
        raise HTTPException(409,{"error":_customer_denial(exc,task_id),"next":"Stop remains pending; verify worker completion and provider usage before Delete."})
    except EngineUnreachableError as exc:
        activity("task.stop.failed","Cooperative stop could not reach the engine",task_id=task_id,actor=a.label,error=type(exc).__name__)
        raise _engine_unreachable(exc)
    except EngineClientError as exc:
        activity("task.stop.failed","Cooperative stop was not confirmed by the engine",task_id=task_id,actor=a.label,error=str(exc.error.detail)[:200])
        raise HTTPException(502,{"error":"Engine Stop was not confirmed.","cause":exc.error.detail,"next":"The durable request remains pending. Retry Stop; do not Delete."})
    activity("task.stop.result","Cooperative stop recorded",task_id=task_id,actor=a.label,outcome=str(out.get("status","ok"))[:40] if isinstance(out,dict) else "ok")
    return out

def _run_control(task_id):
    # Whether a phase of this task is running as its own process here, so the owner can stop it.
    from . import run_slots
    return {"stoppable":bool(config.CLOUD_TENANTS and config.RUN_ISOLATION=="process" and run_slots.is_running(task_id))}


class StopRunRequest(BaseModel):
    confirm: bool = False

STOP_RUN_MESSAGE="Stopping ends this phase now. Work already saved is kept and you can start it again. A model call in progress when you stop may still be counted against your credits."

@app.post("/api/tasks/{task_id}/stop-run")
def stop_run(task_id:str,body:StopRunRequest,a:auth.Actor=Depends(auth.require_actor)):
    # The owner stops their own running phase. This ends the child process; it is not the interview engine's cooperative /stop.
    if not config.CLOUD_TENANTS:raise HTTPException(404,"Not available on this server.")
    if not store.task(task_id):raise HTTPException(404,"task not found")
    if config.RUN_ISOLATION!="process":raise HTTPException(409,{"error":"A run cannot be stopped on this server yet.","cause":"Runs here do not have their own process.","next":"Wait for the run to finish."})
    from . import run_slots
    if not body.confirm:
        return {"status":"preview","running":run_slots.is_running(task_id),"message":STOP_RUN_MESSAGE}
    if not run_slots.request_stop(task_id,"owner",a.label):
        activity("run.stop.refused","Stop refused: nothing is running",task_id=task_id,actor=a.label,reason="not_running")
        raise HTTPException(409,{"error":"Nothing is running to stop.","cause":"The phase has already finished or has not started.","next":"Reload the task."})
    store.audit(a.label,"run.stop_requested",task_id,{"by":"owner"})
    activity("run.stop_requested","Owner asked to stop the run",task_id=task_id)
    return {"status":"stopping","message":"Stopping. This takes a few seconds."}

@app.post("/api/tasks/{task_id}/stop/reconcile")
def reconcile_task_stop(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    if not config.CLOUD_TENANTS:raise HTTPException(404,"cooperative Stop unavailable")
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    if not cloud_execution_admitted():raise HTTPException(503,"Cloud admission and lifecycle validation pending")
    if task['state'] not in ('stop_requested','stopped'):raise HTTPException(409,"durable Stop request required")
    from .cooperative_stop import request_stop
    from .attempt_ledger import AdmissionDenied
    activity("task.stop.requested","Cooperative stop reconcile requested",task_id=task_id,actor=a.label,reconcile=True)
    try:out=request_stop(store,engine_client,task_id,reconcile=True)
    except AdmissionDenied as exc:
        activity("task.stop.refused","Cooperative stop reconcile refused",task_id=task_id,actor=a.label,reason="admission")
        raise HTTPException(409,{"error":_customer_denial(exc,task_id),"next":"Worker completion and usage must be verified before Delete."})
    except EngineUnreachableError as exc:
        activity("task.stop.failed","Cooperative stop reconcile could not reach the engine",task_id=task_id,actor=a.label,error=type(exc).__name__)
        raise _engine_unreachable(exc)
    except EngineClientError as exc:
        activity("task.stop.failed","Engine quiescence was not confirmed",task_id=task_id,actor=a.label,error=str(exc.error.detail)[:200])
        raise HTTPException(502,{"error":"Engine quiescence not confirmed.","cause":exc.error.detail})
    activity("task.stop.result","Cooperative stop reconcile recorded",task_id=task_id,actor=a.label,reconcile=True,outcome=str(out.get("status","ok"))[:40] if isinstance(out,dict) else "ok")
    return out

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    try:store.delete_task(task_id,a.label)
    except KeyError:
        activity("task.delete.refused","Task delete refused: no such task",task_id=task_id,actor=a.label,reason="not_found")
        raise HTTPException(404,{"error":"Task not found.","cause":"It may already have been deleted.","next":"Return to the task list."})
    except ValueError as e:
        activity("task.delete.refused","Task delete refused: active work or founder gate",task_id=task_id,actor=a.label,reason="active_work")
        raise HTTPException(409,{"error":str(e),"cause":"This task still has active work or a founder gate.","next":"Stop or finish the active work before deleting this task. A replay interrupted by restart will be marked stopped on startup."})
    activity("task.deleted","Task deleted",task_id=task_id,actor=a.label)
    return {"status":"deleted","task_id":task_id,"source_artifacts_deleted":False}

class QualityConfigRequest(BaseModel):
    config:dict
def _quality_engine_down(exc):
    return HTTPException(502,{"error":"The interview engine is not reachable.","cause":str(exc),"next":"Start the engine (scripts/run-ui.sh does it), then reload the Debug dialog."})
@app.get("/api/question-quality-config")
def get_question_quality_config(a:auth.Actor=Depends(auth.require_actor)):
    """Global interview question-quality settings; the engine owns the schema and defaults."""
    if config.CLOUD_TENANTS:raise HTTPException(404,"engine configuration is internal")
    try:return engine_client.question_quality_config()
    except EngineUnreachableError as exc:raise _quality_engine_down(exc)
    except EngineClientError as exc:raise HTTPException(502,{"error":"The engine could not return the settings.","cause":exc.error.detail,"next":"Retry; if it repeats, check the engine log."})
@app.put("/api/question-quality-config")
def put_question_quality_config(req:QualityConfigRequest,a:auth.Actor=Depends(auth.require_admin)):
    try:out=engine_client.save_question_quality_config(req.config)
    except QualityConfigRejected as exc:
        activity("settings.save_refused","Question quality settings refused by the engine",actor=a.label,key="question-quality",reason=str(exc)[:200])
        raise HTTPException(422,{"error":str(exc),"cause":"The engine validates every setting before saving.","next":"Fix that setting and save again. Nothing was changed."})
    except EngineUnreachableError as exc:
        activity("settings.save_failed","Question quality settings could not reach the engine",actor=a.label,key="question-quality",error=type(exc).__name__)
        raise _quality_engine_down(exc)
    except EngineClientError as exc:
        activity("settings.save_failed","The engine could not save the question quality settings",actor=a.label,key="question-quality",error=str(exc.error.detail)[:200])
        raise HTTPException(502,{"error":"The engine could not save the settings.","cause":exc.error.detail,"next":"Retry; if it repeats, check the engine log."})
    activity("settings.saved","Question quality settings saved",actor=a.label,key="question-quality")
    return out

class TaskStartRequest(BaseModel):
    model_route:Literal["ollama","deepseek"]|None=None
    initial_model:str|None=None
@app.get("/api/model-routes")
def model_routes(a:auth.Actor=Depends(auth.require_actor)):
    """The engine resolves routes; legacy env keys only seed the initial choice."""
    if config.CLOUD_TENANTS:
        # Display every supported route; availability is bounded by the installed
        # gateway AND the same live engine preflight used by Start.
        from . import cloud_runtime
        gateway=cloud_runtime.gateway_factory()
        open_now=cloud_execution_admitted()
        admitted_models=getattr(gateway,'models',{}) if gateway else {}
        routes=[]
        for route in ('deepseek','ollama'):
            errors=[];models=None;model=None
            if route=='deepseek' and not (os.environ.get('DEEPSEEK_API_KEY') or '').strip():
                errors.append('missing key: DEEPSEEK_API_KEY is not set on this server')
            if route=='ollama' and not local_route.local_preview_enabled():
                errors.append('DELIVERY_LOCAL_PREVIEW is not enabled on this server')
            if not open_now:errors.append('Execution is closed on this server.')
            if gateway is None:errors.append('Admission gateway is not installed on this server.')
            elif route not in admitted_models:errors.append('Admission gateway is configured for '+', '.join(admitted_models)+' only; no reviewed '+route+' route.')
            if not errors:
                try:
                    preview=engine_client.preview_route(route)
                    models=preview['model_map'];pairs={tuple(pair) for pair in models.values()}
                    if len(pairs)!=1:raise ValueError('engine preview returned divergent stage models')
                    provider,model=next(iter(pairs))
                    if provider!=route:raise ValueError(f'engine preview returned {provider} for selected {route}')
                    if model!=admitted_models.get(route):raise ValueError(f'Admission gateway reviewed model is {admitted_models.get(route)}; engine selected {model}.')
                except (EngineClientError,EngineUnreachableError,KeyError,ValueError,TypeError) as exc:
                    errors.append(exc.error.detail if isinstance(exc,EngineClientError) else str(exc))
            routes.append({'id':route,'available':not errors,'managed':True,
                **({'error':' '.join(errors)} if errors else {}),
                **({'model':f'{route}:{model}','model_map':models,'models':[model]} if model else {})})
        enabled=next((r['id'] for r in routes if r['available']),None)
        return {'routes':routes,'default_route':{'route':enabled} if enabled else None,'managed':True}
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
            options=[model];list_status={'availability':'available','scope':'pinned_route'}
            if route=="ollama":
                try:
                    options=engine_client.local_models()
                    list_status={'availability':'available','scope':'installed_models'}
                except (EngineClientError,EngineUnreachableError,AttributeError):
                    list_status={'availability':'unavailable','reason':'installed_lookup_failed','scope':'pinned_route_only'}
            routes.append({"id":route,"available":True,"model":f"{provider}:{model}","model_map":models,"models":options,"model_list":list_status})
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
    if config.CLOUD_TENANTS and selected_route not in local_route.managed_routes():
        raise HTTPException(422,{"error":"That model route is not available on this server.","cause":"managed routes: "+", ".join(local_route.managed_routes()),"next":"Reload the Start screen and choose an available route."})
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
    proposed_session=str(uuid4())
    if not store.begin_interview_start(task_id,proposed_session,selected_route):
        raise HTTPException(409,"task already started")
    spawn(_complete_interview_start(task_id,proposed_session,selected_route,initial_model,idea,effective_models,a.label))
    return JSONResponse({"status":"starting","task_id":task_id,"session_id":proposed_session,
                         "next":f"/tasks/{task_id}/view"},status_code=202)

async def _complete_interview_start(task_id:str,proposed_session:str,selected_route:str,
                                    initial_model:str|None,idea:str,effective_models:dict,actor_label:str):
    progress_stop=asyncio.Event()
    progress_watch=spawn(_watch_engine_advance(task_id,proposed_session,progress_stop))
    engine_started_at=time.monotonic()
    try:
        started=await run_in_threadpool(engine_client.start,StartSessionRequest(task_id=task_id,session_id=UUID(proposed_session),idea=idea,model_route=selected_route,initial_model=initial_model))
        activity('engine.start.returned','Engine Start call returned before final progress drain',task_id=task_id,
                 session_id=proposed_session,dur_ms=int((time.monotonic()-engine_started_at)*1000))
    except EngineUnreachableError as exc:
        activity("engine.unreachable","Interview engine unreachable at task start",task_id=task_id,base_url=exc.base_url,cause=str(exc.cause))
        store.add_event(task_id,"error",f"Error: the interview engine is not reachable. Likely cause: nothing accepted the connection at {exc.base_url} - the engine service is not running. Next: restart scripts/run-ui.sh (it starts the engine automatically), or start the engine by hand: {ENGINE_START_HINT}. Then retry.",phase_key="intent",payload=_engine_unreachable_payload(exc))
        store.update_phase(task_id,"intent","failed",detail=str(exc)[:300])
        return
    except EngineClientError as exc:
        payload={"route":selected_route,"http_status":exc.http_status,"protocol_error":exc.error.model_dump(mode="json"),"stage":"preflight"}
        activity("model.route.preflight.rejected","Interview route rejected by engine",task_id=task_id,**payload)
        store.add_event(task_id,"model_preflight",f"Interview route {selected_route} rejected: {exc.error.detail}",phase_key="intent",payload=payload)
        store.add_event(task_id,"error",f"Interview start failed: {exc.error.detail}",phase_key="intent",payload=payload)
        # Operator-only failure evidence: the engine's sanitized 503 block is
        # recorded on the dedicated diagnostics store - never on the events or
        # audit rows every customer surface reads.
        if getattr(exc,'operator_diagnostics',None):
            try:store.record_failure_diagnostics(task_id,'engine.start',exc.http_status,exc.operator_diagnostics)
            except Exception as record_exc:activity("engine.start.diagnostics_unrecorded","Operator failure diagnostics could not be recorded",task_id=task_id,error=f"{type(record_exc).__name__}")
        store.update_phase(task_id,"intent","failed",detail=exc.error.detail[:300])
        return
    except Exception as exc:
        activity("engine.start.failed","Interview start failed unexpectedly",task_id=task_id,error=f"{type(exc).__name__}: {str(exc)[:200]}")
        store.add_event(task_id,"error",f"Interview start failed: {type(exc).__name__}: {str(exc)[:200]}",phase_key="intent")
        store.update_phase(task_id,"intent","failed",detail=f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    finally:
        drain_started_at=time.monotonic()
        progress_stop.set();await progress_watch
        activity('engine.start.progress_drain','Final Start progress watch completed',task_id=task_id,
                 session_id=proposed_session,dur_ms=int((time.monotonic()-drain_started_at)*1000))
    if started.snapshot.session_id!=proposed_session or started.snapshot.task_id!=task_id:
        store.add_event(task_id,"error","Interview engine returned a different task or session ID",phase_key="intent")
        store.update_phase(task_id,"intent","failed",detail="Engine task or session ID mismatch")
        return
    # The session ID was bound before the long call; do not replace it here.
    effective_models=started.snapshot.model_map or effective_models
    pairs={tuple(pair) for pair in effective_models.values()}
    if len(pairs)!=1:
        store.add_event(task_id,"error","Interview engine returned an inconsistent session model map",phase_key="intent",
                        payload={"session_id":proposed_session,"next":"Check the engine's pinned model route and journal; the session remains bound."})
        store.update_phase(task_id,"intent","failed",detail="Engine returned inconsistent session model map")
        return
    provider,model=next(iter(pairs))
    resolved=f"{provider}:{model}"
    activity("engine.start.response","Engine start response received",task_id=task_id,session_id=started.snapshot.session_id,
             sequence=started.snapshot.sequence,status=int(started.snapshot.process_status),next_step=int(started.snapshot.next_step),
             open_input='null' if started.snapshot.open_input is None else 'set')
    t_persist=time.monotonic()
    # A route switch can arrive while Start is completing; do not roll the
    # founder's newer choice back to the initial engine preview.
    if not (store.task(task_id) or {}).get("model_default"):
        store.set_model_default(task_id,resolved)
    store.add_event(task_id,"model_route",f"interview uses {resolved}",phase_key="intent",payload={"route":selected_route,"resolved":resolved,"provider":provider,"model":model,"effective_stages":list(effective_models),"scope":"start","actor":actor_label})
    activity("model.route.resolved","Interview route accepted",task_id=task_id,session_id=started.snapshot.session_id,selected=selected_route,effective=resolved)
    detail="Waiting for founder input" if started.snapshot.process_status==ProcessStatus.WAITING_INPUT else "Interview engine running"
    store.update_phase(task_id,"intent","running",detail=detail)
    store.add_event(task_id,"engine_protocol","HTTP interview session started",phase_key="intent",payload=started.model_dump(mode="json"))
    activity("engine.start.persisted","Interview start writes committed",task_id=task_id,session_id=started.snapshot.session_id,
             dur_ms=int((time.monotonic()-t_persist)*1000))
    return

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
    if not existing:
        store.set_model_default(task_id,new)
    return changed.model_dump(mode="json")

class InterviewAnswer(BaseModel):
    founder_inputs:list[dict]=Field(default_factory=list,max_length=1000)
    comment:str=Field(default="",max_length=7000)
    review_revision:str|None=Field(default=None,max_length=240)
    answer:str=Field(min_length=1,max_length=8000)
    expected_sequence:int|None=Field(default=None,ge=0)
    decision_id:str|None=Field(default=None,max_length=160)
_REVIEW_SECTION={"constraints":"Constraint","assumptions":"Assumption","assumption_items":"Assumption","functional":"Functional requirement","non_functional":"Non-functional requirement","stakeholder_needs":"Stakeholder need","needs":"Stakeholder need","scenarios":"Scenario","risks":"Risk","glossary":"Glossary term"}
_REVIEW_VERB={"accept":"Kept as written","remove":"Removed","replace":"Replaced"}

def _review_item_label(finding)->str:
    """Founder-readable name of one flagged item: its kind and the start of its wording."""
    path=getattr(finding,"path",None) or []
    head=next((p for p in path if isinstance(p,str)),"")
    kind=_REVIEW_SECTION.get(head) or (head.replace("_"," ").capitalize() if head else "Item")
    original=getattr(finding,"original",None)
    text=original if isinstance(original,str) else next((original[k] for k in ("statement","title","name","description","text") if isinstance(original,dict) and isinstance(original.get(k),str) and original[k].strip()),"")
    text=" ".join(str(text).split())
    if len(text)>90:text=text[:87].rstrip()+"..."
    return f"{kind}: {text}" if text else kind

def semantic_answer_display(review,decisions:list[dict])->str:
    """Founder-readable record of a flagged-input review (never ids or the revision hash)."""
    by_id={f.item_id:f for f in review.findings}
    lines=[]
    for d in decisions:
        finding=by_id.get(d.get("item_id"))
        label=_review_item_label(finding) if finding else "Item"
        lines.append(f"{_REVIEW_VERB.get(d.get('action'),'Reviewed')} - {label}")
    n=len(lines)
    return f"Reviewed {n} flagged item{'' if n==1 else 's'}:\n"+"\n".join(lines) if lines else "Reviewed the flagged items."

@app.post("/api/tasks/{task_id}/answer")
async def answer_interview(task_id:str,body:InterviewAnswer,a:auth.Actor=Depends(auth.require_actor)):
    task_before=store.task(task_id) or {};input_before=runner.input_state(task_id)
    if body.review_revision:
        live=get_task(task_id)['control_snapshot']
        if live.get('wait_id')!=body.decision_id or live.get('revision')!=body.review_revision or live.get('owner')!='engine' or live.get('mode')!='waiting_input':
            raise HTTPException(409,{"error":"Review changed.","cause":"The decision revision is no longer current.","next":"Open the live review before continuing.","stale":True})
        if not store.decision_review(task_id,body.decision_id,body.review_revision):raise HTTPException(409,'review evidence is unavailable')

    recent_answers=[{"event_id":event["id"],"phase_key":event.get("phase_key"),"decision_id":event.get("payload",{}).get("decision_id")} for event in store.events(task_id) if event.get("kind")=="answer"][-10:]
    activity("api.answer.attempt","Founder answer submission received",endpoint=f"/api/tasks/{task_id}/answer",task_id=task_id,task_state=task_before.get("state"),active_phase=task_before.get("active_phase"),submitted_decision_id=body.decision_id,open_decision_id=(input_before.get("decision") or {}).get("decision_id"),process_live=input_before["process_live"],waiting=input_before["waiting"],recent_answer_events=json.dumps(recent_answers,separators=(",",":")))
    answer_display=None
    try:
        work=runner.work_state(task_id)
        if work and work.owner=="decision" and work.phase==task_before.get("active_phase"):
            if not work.input_state()["waiting"]:raise RuntimeError("local decision is no longer waiting for input")
            await runner.send_input(task_id,body.answer,body.decision_id)
            source="local_decision"
        else:
            if work and work.owner in ("decision","requirement"):
                raise RuntimeError("the local decision or requirement has changed; refresh the task")
            if task_before.get("active_phase") not in ("intent","conops"):
                raise RuntimeError("this phase has no interview input; refresh the task")
            session_id=store.engine_session(task_id)
            if not session_id:raise RuntimeError("task has no HTTP engine session")
            state=await run_in_threadpool(engine_client.snapshot,session_id);pending=state.open_input
            if pending is None:raise RuntimeError("task is not waiting for input")
            if body.decision_id and str(pending.input_id)!=body.decision_id:raise RuntimeError("submitted decision does not match live input")
            if body.expected_sequence is not None and state.sequence!=body.expected_sequence:
                raise RuntimeError("session sequence changed; refresh the open input")
            raw_value,separator,rationale=body.answer.partition(" - ")
            raw_value=raw_value.strip();rationale=rationale.strip() if separator else ""
            typed_value=next((option.value for option in pending.options if str(option.value)==raw_value),body.answer)
            if pending.context.get('decision_purpose')=='semantic_review':
                from vmodel_contracts.semantic_review import SemanticReview
                review=SemanticReview.model_validate(pending.context['evidence']['semantic_review'])
                proposal=json.loads(body.answer)
                if proposal.get('revision')!=review.revision:raise RuntimeError('semantic review revision changed')
                decisions=[{**d,'actor':a.label} for d in proposal.get('decisions',[])]
                typed_value=json.dumps({'revision':review.revision,'decisions':decisions})
                answer_display=semantic_answer_display(review,decisions)
            if pending.rationale_required and not rationale:raise RuntimeError("typed input requires a rationale")
            progress_stop=asyncio.Event()
            progress_watch=spawn(_watch_engine_advance(task_id,session_id,progress_stop))
            try:
                submitted=await run_in_threadpool(engine_client.submit,session_id,InputResponse(input_id=pending.input_id,expected_sequence=body.expected_sequence,value=typed_value,
                    rationale=rationale or None,actor_id=a.label,founder_inputs=body.founder_inputs))
            finally:
                progress_stop.set();await progress_watch
            store.add_event(task_id,"engine_protocol","Typed founder input accepted",phase_key=task_before.get("active_phase") or "intent",payload=submitted.model_dump(mode="json"))
            source="http_interview"
            if body.comment.strip():
                store.add_review_comment(task_id,body.decision_id or str(pending.input_id),body.review_revision or f'engine:{state.session_id}:{state.sequence}',a.label,body.comment)
                store.add_event(task_id,'artifact_comment',body.comment,phase_key=task_before.get('active_phase'),
                    payload={'wait_id':body.decision_id,'revision':body.review_revision,'actor':a.label,'comment':body.comment})

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
    answer_payload={"actor":a.label,"character_count":len(body.answer),"transcript":True,"decision_id":body.decision_id,"answer":body.answer}
    if answer_display:answer_payload["answer_display"]=answer_display
    store.add_event(task_id,"answer",f"You: {answer_display or body.answer}",phase_key=active_phase,payload=answer_payload)
    activity("engine.answer","Founder answer submitted",task_id=task_id,phase=active_phase,actor=a.label,character_count=len(body.answer))
    if source=="http_interview":
        if config.CLOUD_TENANTS:_finish_cloud_interview(task_id,submitted.snapshot)
        elif _reconcile_terminal_http_interview(task_id,submitted.snapshot):
            spawn(_continue_reconciled_interview(task_id))
    return {"status":"accepted"}

def _read_task_text(task_id:str,path:Path,*,errors='strict')->str:
    if not config.CLOUD_TENANTS:return path.read_text(errors=errors)
    from vmodel_contracts.runtime import artifacts_root
    with session_reader(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),_recorded_engine_session(task_id),store):
        return read_bound_text(path,errors=errors)

def _project_manifest_rows(rows:list[dict])->list[dict]:
    records=[]
    for row in rows:
        if row['publication_state']!='published':continue
        error=row.get('validation_error')
        records.append({"phase_key":row['phase_key'],"name":row['name'],"title":_ARTIFACT_TITLES.get(row['name'],Path(row['name']).stem.replace("_"," ").title()),
            "ref":row['ref'],"mime_type":row['mime_type'],"stale":bool(row['stale']),"generation":row['generation'],"present":True,
            "valid":row['valid'],"validator":row['validator'],"validation_errors":[error] if error else [],"validation_error":error,
            "byte_size":row['byte_size'],"sha256":row['sha256']})
    return records

def artifact_manifest_coverage(task_id:str)->dict:
    """Operator readiness check before enabling manifest reads: compares the legacy
    projection with the published manifest for one task. Enabling ARTIFACT_MANIFEST_READS
    while 'legacy_only' is non-empty would make existing artifacts vanish, so enable only
    after publication (or backfill) covers them. Both flags default OFF; production is
    unchanged until an approved enable runs this check first."""
    flag=config.ARTIFACT_MANIFEST_READS
    try:
        config.ARTIFACT_MANIFEST_READS=False
        legacy=_task_artifact_snapshot(task_id)
    finally:
        config.ARTIFACT_MANIFEST_READS=flag
    snapshot=store.manifest_snapshot(task_id) if hasattr(store,'manifest_snapshot') else None
    manifest=_project_manifest_rows(snapshot['rows']) if snapshot else []
    legacy_names={f"{r['phase_key']}/{r['name']}" for r in legacy}
    manifest_names={f"{r['phase_key']}/{r['name']}" for r in manifest}
    return {"task_id":task_id,"published":sorted(manifest_names),"legacy_only":sorted(legacy_names-manifest_names),"ready":not (legacy_names-manifest_names)}

def _task_artifact_snapshot(task_id:str, *, task=None, session=_SESSION_UNSET, events=None, stored_artifacts=None, manifest=_SESSION_UNSET)->list[dict]:
    """Project real session files into task artifacts; filesystem is source evidence."""
    if config.CLOUD_TENANTS and config.ARTIFACT_MANIFEST_READS:
        # Readiness gate, enforced on the read path: the manifest is authority only for
        # a task+generation whose producer/backfill published a COMPLETE validated
        # revision and marked it ready in the same transaction. A ready revision is
        # served purely from the database - no per-request filesystem scan. Without the
        # marker the task is migration-pending: the legacy projection is consulted once
        # to distinguish 'no artifacts yet' from 'uncovered legacy', which fails loudly
        # instead of presenting a partial manifest (even a non-empty one) as complete.
        try:snapshot=store.manifest_snapshot(task_id) if manifest is _SESSION_UNSET else manifest
        except ValueError:
            log.error('manifest readiness marker corrupt task=%s',task_id)
            raise HTTPException(500,'artifact manifest failed its integrity check; the failure was recorded for diagnostics')
        if snapshot is not None:
            return _project_manifest_rows(snapshot['rows'])
        flag=config.ARTIFACT_MANIFEST_READS
        try:
            config.ARTIFACT_MANIFEST_READS=False
            legacy=_task_artifact_snapshot(task_id,task=task,session=session,events=events,stored_artifacts=stored_artifacts)
        finally:
            config.ARTIFACT_MANIFEST_READS=flag
        if legacy:
            log.error('manifest coverage incomplete task=%s legacy=%d (no ready marker)',task_id,len(legacy))
            raise HTTPException(500,'artifact manifest coverage is incomplete for this task; reads stay disabled until publication or backfill covers the legacy artifacts')
        return []
    if task is None:task=store.task(task_id)
    if not task:return []
    session=_recorded_engine_session(task_id,recorded=session,events=events)
    if not session:return stored_artifacts if stored_artifacts is not None else store.task_artifacts(task_id)
    from vmodel_contracts.runtime import artifacts_root
    root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)
    if config.CLOUD_TENANTS and config.ARTIFACT_RESTORE:
        # A second host boots with an empty session root: committed phase artifacts
        # are restored from the published manifest before the filesystem projection.
        from . import artifact_restore
        try:artifact_restore.restore_completed_phases(store,task_id,root)
        except artifact_restore.RestoreRefused as exc:
            raise HTTPException(409,f'completed-phase restore refused: {exc}')
    from .phases import PHASE_DEFINITIONS
    records=[]
    from .artifact_publication import ARTIFACT_VALIDATORS
    validators=ARTIFACT_VALIDATORS
    for phase in PHASE_DEFINITIONS:
        folder=root/("interview" if phase.key in ("intent","conops") else "spec")
        for filename in phase.artifacts:
            path=folder/filename
            if not path.is_file():continue
            valid=None;errors=[];validator=validators.get(filename)
            if validator:
                try:
                    if validator[1] in ("AcceptanceTest[]", "ItemSpec[]"):
                        from pydantic import TypeAdapter
                        if validator[1]=="AcceptanceTest[]":
                            from vmodel_contracts.verification import AcceptanceTest
                            item_type=AcceptanceTest
                        else:
                            from vmodel_contracts.spec import ItemSpec
                            item_type=ItemSpec
                        TypeAdapter(list[item_type]).validate_json(_read_task_text(task_id,path))
                    else:
                        model=getattr(__import__(validator[0],fromlist=[validator[1]]),validator[1]);model.model_validate_json(_read_task_text(task_id,path))
                    valid=True
                except Exception as exc:valid=False;errors=[f"{type(exc).__name__}: {str(exc)[:600]}"]
            records.append({"phase_key":phase.key,"name":filename,"title":_ARTIFACT_TITLES.get(filename,Path(filename).stem.replace("_"," ").title()),"ref":str(path),"mime_type":mimetypes.guess_type(path.name)[0] or "application/octet-stream","stale":False,"generation":task["generation"],"present":True,"valid":valid,"validator":validator[1] if validator else None,"validation_errors":errors,"validation_error":errors[0] if errors else None})
    return records if config.CLOUD_TENANTS else records or (stored_artifacts if stored_artifacts is not None else store.task_artifacts(task_id))

def _task_artifact(task_id:str,phase:str,name:str)->Path:
    artifact=next((a for a in _task_artifact_snapshot(task_id) if a["phase_key"]==phase and a["name"]==name),None)
    if not artifact:raise HTTPException(404,"artifact not found")
    path=Path(artifact["ref"]).resolve()
    if not path.is_file():raise HTTPException(404,"Artifact files are not part of a local preview" if os.environ.get("DELIVERY_LOCAL_PREVIEW")=="1" else "artifact file not found")
    return path

_OUTPUT_PARTIALS={"srs":"srs_fills.partial.json","architecture":"architecture.partial.json",
                  "specs":"items.partial.json","tests":"tests_fills.partial.json",
                  "validation":"validation_fills.partial.json"}

@app.get("/api/tasks/{task_id}/phase-output")
def task_phase_output(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    """Current phase output only. Partial checkpoints are atomic producer writes, not control."""
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    phase=task.get("active_phase");generation=task.get("generation")
    if os.environ.get("DELIVERY_LOCAL_PREVIEW")=="1":return {"phase_key":phase,"generation":generation,"status":"local_preview","items":[]}
    session=_recorded_engine_session(task_id)
    if not session or not phase:return {"phase_key":phase,"generation":generation,"status":"waiting","items":[]}
    from vmodel_contracts.runtime import artifacts_root
    root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)
    folder=root/("interview" if phase in ("intent","conops") else "spec")
    # Reads are constrained to the recorded session and exact known filenames.
    from .phases import PHASE_BY_KEY
    names=PHASE_BY_KEY[phase].artifacts
    # The task read projects filesystem records with the task's *current* generation.
    # A file left on disk after a rerun must not thereby acquire that generation.
    def fresh(record, key):
        row=next((p for p in task.get("phases",[]) if p.get("phase_key")==key),{})
        if row.get("state") not in ("running","awaiting_approval","approved","done"):
            return False
        try:
            path=Path(record["ref"])
            started=float(row.get("started_at") or 0)
            return path.is_file() and (not started or path.stat().st_mtime >= started)
        except (OSError,TypeError,ValueError):
            return False
    records=_task_artifact_snapshot(task_id)
    current=[x for x in records if x["phase_key"]==phase and not x.get("stale")
             and x.get("generation")==generation and x.get("present") and x.get("valid") is True
             and x["name"] in names and fresh(x,phase)]
    final=next(iter(current),None)
    invalid_current=any(x["phase_key"]==phase and x["name"] in names and x.get("valid") is False
                        and x.get("generation")==generation and fresh(x,phase) for x in records)
    source=Path(final["ref"]) if final else folder/_OUTPUT_PARTIALS.get(phase,"__no_partial__")
    display_phase=phase
    active_row=next((x for x in task.get("phases",[]) if x.get("phase_key")==phase),{})
    # Partial files do not carry a generation field; reject an older file after rerun.
    if not final and source.is_file() and source.stat().st_mtime < float(active_row.get("started_at") or 0):
        source=folder/"__stale_partial__"
    if not final and source.is_file():
        try:
            partial=json.loads(_read_task_text(task_id,source))
            if ((phase=="architecture" and isinstance(partial,dict)
                 and not any(partial.get(k) for k in ("stack_entries","component_bodies","adr_bodies")))
                or (phase in ("srs","tests","validation") and isinstance(partial,dict) and not partial)
                or (phase=="specs" and isinstance(partial,list) and not partial)):
                source=folder/"__empty_partial__"
        except (OSError,ValueError):
            pass  # A malformed checkpoint is shown as unavailable, not silently replaced.
    if not source.is_file():
        if invalid_current:
            return {"phase_key":phase,"generation":generation,"status":"unavailable","items":[]}
        from .phases import PHASE_DEFINITIONS, PHASE_POSITIONS
        position=PHASE_POSITIONS[phase]
        if position:
            previous=PHASE_DEFINITIONS[position-1]
            prior=[x for x in records if x["phase_key"]==previous.key
                   and x.get("generation")==generation and not x.get("stale") and x.get("present")
                   and x.get("valid") is True and x["name"] in previous.artifacts and fresh(x,previous.key)]
            if not prior:
                # The producer may have written the canonical file without a legacy
                # task_artifacts row. Use the same contract validator as the task read.
                from .phases import PHASE_BY_KEY
                from pydantic import TypeAdapter
                validators={"intent_model.json":("vmodel_contracts.interview","IntentModel"),
                            "conops.json":("vmodel_contracts.conops","ConOpsDocument"),
                            "needs.json":("vmodel_contracts.needs","StakeholderNeedsSet"),
                            "srs.json":("vmodel_contracts.srs","SRSDocument"),
                            "architecture.json":("vmodel_contracts.architecture","ArchitectureOverview")}
                for name in PHASE_BY_KEY[previous.key].artifacts:
                    candidate=root/("interview" if previous.key in ("intent","conops") else "spec")/name
                    if not candidate.is_file() or not fresh({"ref":str(candidate)},previous.key):continue
                    try:
                        if name in validators:
                            namespace,model=validators[name]
                            getattr(__import__(namespace,fromlist=[model]),model).model_validate_json(_read_task_text(task_id,candidate))
                        elif name in ("items.json","tests.json","test_specifications.json"):
                            namespace,model=("vmodel_contracts.spec","ItemSpec") if name=="items.json" else ("vmodel_contracts.verification","AcceptanceTest")
                            TypeAdapter(list[getattr(__import__(namespace,fromlist=[model]),model)]).validate_json(_read_task_text(task_id,candidate))
                        elif name=="spec_items.json":
                            from vmodel_contracts.spec import SpecBundle
                            SpecBundle.model_validate_json(_read_task_text(task_id,candidate))
                        else:continue
                        prior=[{"ref":str(candidate),"name":name,"valid":True}]
                        break
                    except (OSError,ValueError):continue
            if prior and not prior[0].get("valid"):
                prior=[]
            if prior:
                source=Path(prior[0]["ref"])
                display_phase=previous.key
                final=prior[0]
                folder=root/("interview" if display_phase in ("intent","conops") else "spec")
        if not source.is_file():return {"phase_key":phase,"generation":generation,"status":"waiting","items":[]}
    # No traversal and no outside-session file can be used as a phase output.
    if source.resolve().parent!=folder.resolve():raise HTTPException(409,"phase output source outside session")
    try:data=json.loads(_read_task_text(task_id,source))
    except (OSError,ValueError):return {"phase_key":phase,"generation":generation,"status":"unavailable","items":[]}
    if final:
        if display_phase=="srs":rows=[*data.get("functional",[]),*data.get("non_functional",[])] if isinstance(data,dict) else []
        elif display_phase=="needs":rows=data.get("needs",[]) if isinstance(data,dict) else []
        elif display_phase=="architecture":rows=[*data.get("decisions",[]),*data.get("components",[])] if isinstance(data,dict) else []
        elif display_phase in ("specs","tests","validation"):rows=data if isinstance(data,list) else data.get("items",[]) if isinstance(data,dict) else []
        elif display_phase=="conops":rows=[*data.get("operational_scenarios",[]),*data.get("concept_options",[])] if isinstance(data,dict) else []
        else:rows=[data] if isinstance(data,dict) else []
    elif phase=="architecture" and isinstance(data,dict):
        rows=[]
        for key in ("stack_entries","component_bodies","adr_bodies"):
            values=data.get(key) or {}
            if isinstance(values,dict):
                rows.extend({"id":str(identity),**value} for identity,value in values.items() if isinstance(value,dict))
    elif phase=="specs" and isinstance(data,list):rows=data
    elif phase=="validation" and isinstance(data,dict):
        rows=[item for value in data.values() if isinstance(value,dict) for item in value.get("items") or [] if isinstance(item,dict)]
    elif phase in ("srs","tests") and isinstance(data,dict):
        rows=[{"id":key,**value} for key,value in data.items() if isinstance(value,dict)]
    else:rows=[]
    if not isinstance(rows,list):rows=[]
    if not final and not rows and display_phase==phase:
        return {"phase_key":phase,"generation":generation,"status":"waiting","items":[]}
    # Persisted generator output is data, not trusted markup. Bound its size and
    # shape before the browser interpolates it into the phase panel.
    rows=[x for x in rows if isinstance(x,dict)][:500]
    return {"phase_key":phase,"display_phase":display_phase,"generation":generation,
            "status":"previous_phase" if display_phase!=phase else "complete" if final else "in_progress",
            "source":source.name,"items":rows[:500]}

@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}")
def task_artifact_download(task_id:str,phase:str,name:str):
    path=_task_artifact(task_id,phase,name)
    if config.CLOUD_TENANTS and config.ARTIFACT_MANIFEST_READS:
        from fastapi.responses import Response
        # Error mapping: genuinely missing or foreign-invisible artifact is 404; a
        # corrupt/unverifiable payload is an explicit 500 (never a plausible missing
        # record); database failures propagate as 5xx. No filesystem fallback.
        try: data=store.read_artifact_bytes(task_id,phase,name)
        except KeyError:
            activity("task.export.refused","Artifact download refused: not found",task_id=task_id,export="artifact",reason="not_found")
            raise HTTPException(404,'artifact not found')
        except ValueError:
            log.exception('artifact integrity failure task=%s phase=%s name=%s',task_id,phase,name)
            raise HTTPException(500,'artifact failed its stored integrity check; the failure was recorded for diagnostics')
        activity("task.export","Artifact downloaded",task_id=task_id,export="artifact",phase=phase,name=name)
        return Response(data,media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                        headers={'Content-Disposition':f'attachment; filename="{name}"'})
    if config.CLOUD_TENANTS:
        from .tenant_files import read_session_bytes
        from vmodel_contracts.runtime import artifacts_root
        from fastapi.responses import Response
        base=artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR'));session=_recorded_engine_session(task_id)
        root=authorized_session(base,session,store)
        data=read_session_bytes(base,session,str(path.relative_to(root)),store)
        activity("task.export","Artifact downloaded",task_id=task_id,export="artifact",phase=phase,name=name)
        return Response(data,media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                        headers={'Content-Disposition':f'attachment; filename="{name}"'})
    activity("task.export","Artifact downloaded",task_id=task_id,export="artifact",phase=phase,name=name)
    return FileResponse(path,filename=name)

_ARTIFACT_TITLES={"srs.semantic-review.json":"Requirements Semantic Review","architecture.decisions.json":"Architecture Stack Decisions","intent_model.json":"Needs and Scope","conops.json":"Concept of Operations","needs.json":"Stakeholder Needs","srs.json":"Requirements","architecture.json":"Architecture","items.json":"Item Specifications","spec_items.json":"Item Specifications","tests.json":"Verification","test_specifications.json":"Verification","validation.json":"Validation"}

def _checkpoint_interview_turns(task_id:str,status=None)->list[tuple[str,str]]:
    """Recover historical turns when old API events stored only answer lengths."""
    def mark(availability,reason):
        if status is not None:status['checkpoint_transcript']={'availability':availability,'reason':reason}
    mark('available','saved_checkpoint')
    if config.CLOUD_TENANTS:
        mark('policy_excluded','cloud_legacy_checkpoint_excluded');return []  # Never deserialize legacy local checkpoint blobs from a customer read.
    session=_recorded_engine_session(task_id)
    if not session:mark('absent','no_recorded_session');return []
    from vmodel_contracts.runtime import artifacts_root
    db=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"/"checkpoints.sqlite"
    if not db.is_file():mark('absent','no_saved_checkpoint');return []
    built=os.environ.get("REQUI_RE_DIST")=="1"
    engine_dir=(Path.cwd() if built else Path(os.environ.get("INTERVIEW_ENGINE_DIR",str(Path(__file__).resolve().parents[3]/"interview-engine"))).resolve())
    python=Path(os.environ.get("INTERVIEW_ENGINE_PYTHON",sys.executable if built else str(engine_dir/".venv/bin/python")))
    if not python.is_file():mark('unavailable','recovery_runtime_missing');return []
    script="import json,sqlite3,sys\nfrom langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer\nfrom interview_engine.contracts import AppArchetype\nrow=sqlite3.connect(sys.argv[1]).execute('select type,checkpoint from checkpoints order by checkpoint_id desc limit 1').fetchone()\nif not row: print('[]');raise SystemExit\nobj=JsonPlusSerializer(allowed_msgpack_modules=[AppArchetype]).loads_typed((row[0],row[1]))\nmessages=(obj.get('channel_values') or {}).get('messages') or []\nout=[]\nfor m in messages:\n role=getattr(m,'type',None) or (m.get('type') if isinstance(m,dict) else None)\n content=getattr(m,'content',None) or (m.get('content') if isinstance(m,dict) else None)\n if role in ('ai','human') and isinstance(content,str):out.append([role,content])\nprint(json.dumps(out))"
    try:
        env={**os.environ,**({} if built else {"PYTHONPATH":str(engine_dir/"src")+os.pathsep+os.environ.get("PYTHONPATH","")})}
        result=subprocess.run([str(python),"-c",script,str(db)],capture_output=True,text=True,timeout=8,env=env)
        if result.returncode:mark('unavailable','recovery_failed');return []
        messages=json.loads(result.stdout);turns=[];question=None
        for role,content in messages:
            if role=="ai":question=content.replace("[INTERVIEW COMPLETE]","").strip()
            elif role=="human" and question:turns.append((question,content.strip()));question=None
        return turns
    except Exception:mark('unavailable','recovery_failed');return []

def _interview_model_provenance(task_id:str,status=None)->list[str]:
    """Usage is per-call truth; never collapse a switched stage to its first model."""
    def mark(availability,reason):
        if status is not None:status['model_usage']={'availability':availability,'reason':reason}
    mark('absent','no_saved_usage')
    if config.CLOUD_TENANTS:mark('policy_excluded','cloud_local_usage_excluded');return []
    session=_recorded_engine_session(task_id)
    rows=[]
    if session:
        try:
            from vmodel_contracts.runtime import artifacts_root
            usage=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"/"usage.jsonl"
            if usage.is_file():
                rows=[json.loads(line) for line in usage.read_text().splitlines() if line.strip()]
                if any(not isinstance(row,dict) for row in rows):raise ValueError('invalid usage row')
                mark('available','saved_usage')
        except (ValueError,OSError):mark('unavailable','invalid_or_unreadable');rows=[]
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
    for event in _customer_events(store.events(task_id)):
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
    supplemental={}
    checkpoint_turns=_checkpoint_interview_turns(task_id,supplemental)
    if checkpoint_turns:complete=checkpoint_turns
    route_changes=[e for e in _customer_events(store.events(task_id)) if e.get("kind")=="model_route" and (e.get("payload") or {}).get("scope")=="switch"]
    models=_interview_model_provenance(task_id,supplemental)
    notes=[f"Supplemental {name}: {row['availability']} ({row['reason']}). Primary artifact and recorded events are preserved." for name,row in supplemental.items() if row['availability'] in ('unavailable','absent')]
    if not complete and not route_changes and not notes:return ""
    lines=models+notes+["","## Interview questions and answers"]
    # Keep switch markers in event order, between the questions and answers
    # they actually separate. Checkpoint-only legacy transcripts fall back to
    # the reconstructed complete turns.
    numbered=0
    if not checkpoint_turns:
        for event in _customer_events(store.events(task_id)):
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


def _decision_provenance(task_id:str,architecture:dict)->list[dict]:
    """Derived, conservative provenance. Empty baseline refs do not prove founder intent."""
    session=_recorded_engine_session(task_id)
    from vmodel_contracts.runtime import artifacts_root
    root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview" if session else None
    preferences=[]
    preference_status={'availability':'absent','reason':'no_saved_intent'}
    if root:
        path=root/"intent_model.json"
        if path.is_file():
            try:
                from vmodel_contracts.interview import IntentModel
                intent=IntentModel.model_validate_json(path.read_text())
                preference_status={'availability':'available','reason':'saved_intent'}
                preferences=[{"area":p.area.value,"preference":p.preference,"strength":p.strength.value,
                              "source_turns":p.source_turns} for p in intent.tech_preferences]
            except (OSError,ValueError):preference_status={'availability':'unavailable','reason':'invalid_or_unreadable'}
    result=[]
    for decision in architecture.get("decisions",[]):
        if not isinstance(decision,dict):continue
        refs=[str(x) for x in decision.get("baseline_refs") or []]
        # Canonical baseline references identify inherited system facts, not founder speech.
        supported=[]
        for selection in decision.get("technology_selections") or []:
            if not isinstance(selection,dict):continue
            name=str(selection.get("display_name") or "").strip().casefold()
            slot=str(selection.get("slot_id") or "").removeprefix("slot:")
            for pref in preferences:
                if pref["source_turns"] and pref["area"]==slot and pref["preference"].strip().casefold()==name:
                    supported.append({"preference":pref["preference"],"area":pref["area"],
                                      "source_turns":pref["source_turns"],"strength":pref["strength"]})
        label="Founder-sourced preference" if supported else "Model-proposed decision"
        result.append({"decision_id":decision.get("decision_id"),"title":decision.get("title"),
                       "label":label,"founder_evidence":supported,"baseline_refs":refs,"evidence_status":preference_status})
    return result

def _validation_panel_for(task_id:str,raw:str)->dict|None:
    """Validation items grouped by need, built from the session's own artifacts."""
    from .validation_panel import validation_panel
    availability={}
    def load(phase,name):
        try:
            value=json.loads(_task_artifact(task_id,phase,name).read_text())
            availability[phase]={'availability':'available'};return value
        except HTTPException:
            availability[phase]={'availability':'absent','reason':'no_saved_artifact'};return None
        except (OSError,ValueError):
            availability[phase]={'availability':'unavailable','reason':'invalid_or_unreadable'};return None
    try:validation=json.loads(raw)
    except ValueError:return None
    ledger=None
    session=_recorded_engine_session(task_id)
    if session:
        from vmodel_contracts.runtime import artifacts_root
        try:ledger=json.loads((authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"spec"/"findings-ledger.json").read_text())
        except FileNotFoundError:availability['findings_ledger']={'availability':'absent','reason':'no_saved_ledger'};ledger=None
        except (OSError,ValueError):availability['findings_ledger']={'availability':'unavailable','reason':'invalid_or_unreadable'};ledger=None
    panel=validation_panel(validation,load("needs","needs.json"),load("tests","tests.json"),ledger)
    if isinstance(panel,dict):panel['supplemental_availability']=availability
    return panel

@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}/view")
def task_artifact_view(task_id:str,phase:str,name:str):
    path=_task_artifact(task_id,phase,name)
    if path.stat().st_size>2_000_000:raise HTTPException(413,"artifact is too large for inline viewing")
    text=_read_task_text(task_id,path,errors="replace");raw=text;display_path=path
    if path.suffix==".json":
        sibling=path.with_suffix(".md")
        if sibling.is_file():display_path=sibling;text=_read_task_text(task_id,sibling,errors="replace")
        try:raw=json.dumps(json.loads(raw),indent=2)
        except Exception:pass
    display_format="markdown" if display_path.suffix==".md" else ("json" if path.suffix==".json" else "text")
    transcript=_interview_transcript_markdown(task_id) if name=="intent_model.json" else ""
    if transcript:
        if display_format!="markdown":text=f"# {_ARTIFACT_TITLES[name]}\n\n"+text;display_format="markdown"
        text=text.rstrip()+"\n\n"+transcript+"\n"
    provenance=_decision_provenance(task_id,json.loads(raw)) if name=="architecture.json" else None
    panel=_validation_panel_for(task_id,raw) if name=="validation.json" else None
    return {"name":name,"title":_ARTIFACT_TITLES.get(name,Path(name).stem.replace("_"," ").title()),"phase":phase,"format":display_format,"content":text,"raw_format":"json" if path.suffix==".json" else "text","raw_content":raw,"display_name":display_path.name,"decision_provenance":provenance,"validation_panel":panel}

@app.get("/api/tasks/{task_id}/artifacts.zip")
def task_artifacts_zip(task_id:str):
    if os.environ.get("DELIVERY_LOCAL_PREVIEW")=="1":raise HTTPException(404,"Artifact downloads are not part of a local preview")
    records=_task_artifact_snapshot(task_id)
    if not records:
        activity("task.export.refused","Artifacts zip refused: no artifacts",task_id=task_id,export="zip",reason="no_artifacts")
        raise HTTPException(404,"no artifacts found")
    data=io.BytesIO()
    with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as archive:
        for artifact in records:
            entry=f"{artifact['phase_key']}/{artifact['name']}"
            if config.CLOUD_TENANTS and config.ARTIFACT_MANIFEST_READS:
                # Read by the snapshot row's content hash: the whole zip is one
                # published revision, never a mix of generations under concurrent publish.
                try: archive.writestr(entry,store.read_artifact_bytes_sha(artifact['sha256']))
                except (KeyError,ValueError):
                    log.exception('artifact integrity failure in zip task=%s entry=%s',task_id,entry)
                    activity("task.export.failed","Artifacts zip failed an integrity check",task_id=task_id,export="zip",reason="integrity")
                    raise HTTPException(500,'an artifact failed its stored integrity check; the failure was recorded for diagnostics')
            elif config.CLOUD_TENANTS:
                from .tenant_files import read_session_bytes
                from vmodel_contracts.runtime import artifacts_root
                base=artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR'));session=_recorded_engine_session(task_id)
                root=authorized_session(base,session,store)
                archive.writestr(entry,read_session_bytes(base,session,str(Path(artifact['ref']).relative_to(root)),store))
            else:archive.write(artifact['ref'],entry)
    from fastapi.responses import Response
    title=(store.task(task_id) or {}).get("title","task").replace(" ","-").lower()
    activity("task.export","Artifacts zip downloaded",task_id=task_id,export="zip",artifacts=len(records))
    return Response(data.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{title}-artifacts.zip"'})

_STRUCTURED_INPUT_KINDS=(InputKind.CHOICE,InputKind.APPROVAL)

def _sync_waiting_input_event(task_id:str,pending:InputRequest,*,events=None,task=None)->None:
    """Record each distinct engine input request once, so the question panel,
    the interview transcript and SSE state refreshes have durable evidence in
    HTTP mode (the subprocess runner used to emit these itself)."""
    input_id=str(pending.input_id)
    if any(event.get("kind")=="waiting_input" and str(event.get("payload",{}).get("input_id"))==input_id for event in (store.events(task_id) if events is None else events)):
        return
    phase=((store.task(task_id) if task is None else task) or {}).get("active_phase") or "intent"
    payload={"waiting_input":True,"question":pending.prompt,"input_id":input_id,"kind":int(pending.kind)}
    store.ensure_waiting_input_event(task_id,pending.prompt,phase_key=phase,payload=payload)
    if events is not None:
        # Include the durable event written during this read in THIS response,
        # rather than returning the pre-sync list until the next refresh.
        # Re-read only after writing, keeping the store's exact timestamp and
        # row shape. Steady-state reads do not need another query.
        events[:]=store.events(task_id)

def _http_input_state(task_id:str,engine_state:EngineSnapshot,*,events=None,task=None)->dict:
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
        if pending.kind in _STRUCTURED_INPUT_KINDS or pending.context.get('decision_purpose')=='semantic_review':decision=pending.model_dump(mode="json")
        else:question=pending.prompt
        _sync_waiting_input_event(task_id,pending,events=events,task=task)
    return {"waiting":waiting,"engine_process_status":int(engine_state.process_status),
            "process_live":engine_state.process_status in (ProcessStatus.RUNNING,ProcessStatus.WAITING_INPUT),
            "decision":decision,"requirement":None,"question":question,"input_id":input_id}

def _cloud_engine_read(task_id:str,task:dict,session_id:str,*,events=None):
    """Owner-session engine snapshot for a cloud task read.

    Read-only: it shows the open question and never reconciles or starts work,
    so a page refresh cannot launch the next phase. Engine errors become
    visible fields on input_state instead of failing the whole task read."""
    empty={"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None}
    try:snapshot=engine_client.snapshot(session_id)
    except EngineClientError as exc:
        return None,{**empty,"engine_error":{"http_status":exc.http_status,"code":int(exc.error.code),"detail":exc.error.detail}}
    except EngineUnreachableError:
        return None,{**empty,"engine_unreachable":True}
    if task.get("active_phase") in ("intent","conops"):return snapshot,_http_input_state(task_id,snapshot,events=events,task=task)
    return snapshot,{**empty,"engine_process_status":int(snapshot.process_status)}

@app.get("/api/tasks/{task_id}/diagnostics")
def task_diagnostics(task_id:str):
    """Everything needed to diagnose this task's interview in one owner-scoped document."""
    from . import task_diagnostics as diagnostics
    if not store.task(task_id):raise HTTPException(404,"task not found")
    detail=get_task(task_id)
    session_id=store.engine_session(task_id);snapshot=None;error=None
    if session_id:
        try:snapshot=engine_client.snapshot(session_id)
        except EngineClientError as exc:error=f"engine answered HTTP {exc.http_status}: {exc.error.detail}"
        except EngineUnreachableError:error="the interview engine is not reachable"
    model_calls=None
    if session_id:
        from vmodel_contracts.runtime import artifacts_root
        root=authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session_id,store)
        model_calls=diagnostics.model_calls(root/"interview",cloud=config.CLOUD_TENANTS)
    return diagnostics.build(detail,detail.get("events") or [],detail.get("control_snapshot"),detail.get("input_state"),
                             diagnostics.engine_summary(snapshot,error),worker_state=store.task_worker_state(task_id) if config.CLOUD_TENANTS else None,model_calls=model_calls)

@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, background_tasks: BackgroundTasks = None, request: Request = None):
    # One snapshot statement on a cold read (was 3: task + engine_session +
    # events), zero on a warm one (task_read_cache; 60s cross-process safety).
    from . import task_read_cache
    # Only the HTTP presentation read may use stale data. Internal callers
    # (answer/gate preconditions, exports) always read fresh authority.
    use_cache = request is not None and request.method == "GET" and request.url.path == f"/api/tasks/{task_id}"
    cached=task_read_cache.get(store,task_id) if use_cache else None
    if cached is not None:
        read_snapshot=cached
    else:
        version=task_read_cache.version(store,task_id)
        try:read_snapshot=store.task_snapshot(task_id)
        except ValueError as exc:
            if 'readiness marker' not in str(exc):raise
            log.error('manifest readiness marker corrupt task=%s',task_id)
            raise HTTPException(500,'artifact manifest failed its integrity check; the failure was recorded for diagnostics') from exc
        if read_snapshot is None: raise HTTPException(404, "task not found")
        if use_cache:task_read_cache.put(store,task_id,read_snapshot,read_version=version)
    task,task_events=read_snapshot["task"],read_snapshot["events"]
    session_id=task.get("engine_session_id")   # the column rides the task row (was a separate statement)
    snapshot=None
    starting=bool(session_id and task.get("active_phase")=="intent" and
                  any(e.get("kind")=="command" and e.get("message")=="Interview starting" and
                      (e.get("payload") or {}).get("session_id")==session_id for e in task_events) and
                  not any(e.get("kind")=="engine_protocol" and
                          (e.get("payload") or {}).get("snapshot",{}).get("session_id")==session_id
                          for e in task_events))
    if starting and task.get("state")=="running":
        input_state={"waiting":False,"process_live":True,"decision":None,"requirement":None,"question":None,"input_id":None}
    elif session_id and config.CLOUD_TENANTS:
        snapshot,input_state=_cloud_engine_read(task_id,task,session_id,events=task_events)
    elif session_id:
        try:
            snapshot=engine_client.snapshot(session_id)
            if _reconcile_terminal_http_interview(task_id,snapshot):
                activity("engine.recovery.reconciled","Restored completed interview boundary from task read; queuing Stakeholder needs",task_id=task_id,session_id=session_id,sequence=snapshot.sequence,next_phase="needs")
                if background_tasks is not None:background_tasks.add_task(_continue_reconciled_interview,task_id)
                task=store.task(task_id)
            input_state=(_http_input_state(task_id,snapshot,events=task_events,task=task) if task.get("active_phase") in ("intent","conops")
                         else {**runner.input_state(task_id),"engine_process_status":int(snapshot.process_status)})
        except EngineClientError as exc:
            input_state={"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None,
                         "engine_error":{"http_status":exc.http_status,"code":int(exc.error.code),"detail":exc.error.detail}}
        except EngineUnreachableError:
            input_state={"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None,"engine_unreachable":True}
    else:input_state=({"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None} if config.CLOUD_TENANTS else runner.input_state(task_id))
    work=None if config.CLOUD_TENANTS else runner.work_state(task_id)
    work_state=({"owner":work.owner,"phase":work.phase,"status":work.status,"started_at":work.started_at,"durable_ref":work.durable_ref,"requirement":work.requirement} if work else None)
    artifacts=_task_artifact_snapshot(task_id,task=task,session=session_id,events=task_events,stored_artifacts=read_snapshot["artifacts"],manifest=read_snapshot["manifest"]);session=_recorded_engine_session(task_id,recorded=session_id,events=task_events)
    from .phases import PHASE_DEFINITIONS
    phase_config={phase.key:{"executor":phase.executor,"generator_stage":phase.generator_stage,
                             "prerequisite":phase.prerequisite,"artifacts":list(phase.artifacts)} for phase in PHASE_DEFINITIONS}
    from .control_snapshot import project as project_control
    from . import phase_start
    ready_next=phase_start.PHASE if config.CLOUD_TENANTS and session_id and phase_start.eligible(task) else None
    control=project_control(task,input_state,snapshot,work,artifacts,ready_next=ready_next)
    _retain_decision_control(task_id,control.model_dump(mode="json"))
    if config.CLOUD_TENANTS:
        control.artifact_ref=None
    if config.CLOUD_TENANTS:
        artifacts=[{key:value for key,value in row.items() if key!='ref'} for row in artifacts]
    return _customer_events({**task, "rerun_control":_rerun_control(), "control_snapshot":control.model_dump(mode="json"), "phase_config":phase_config, "events": _customer_events(task_events), "artifacts":artifacts,"artifact_projection":{"session":session,"root":None if config.CLOUD_TENANTS else str((authorized_session(__import__('vmodel_contracts.runtime',fromlist=['artifacts_root']).artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store)) if session else ''),"count":len(artifacts),"reason":"projected canonical session artifacts" if artifacts else ("session could not be recovered" if not session else "no configured artifacts found under session")}, "input_state":input_state,"active_work":work_state,"stop_control":{"available":bool(config.CLOUD_TENANTS and cloud_execution_admitted() and session_id),"requested":task.get("state") in ("stop_requested","stopped")},"run_control":_run_control(task_id)})

def _retain_decision_control(task_id:str,control:dict)->None:
    decision=control.get('decision') or {};context=decision.get('context') or {}
    if control.get('owner')!='engine' or control.get('mode')!='waiting_input':return
    if context.get('decision_purpose') not in ('conops_concept','interview_approval','semantic_review'):return
    if context.get('evidence_version')!=1 or not context.get('evidence') or not context.get('evidence_revision'):return
    import hashlib
    raw=json.dumps(context['evidence'],sort_keys=True,separators=(',',':'),ensure_ascii=False)
    if len(raw.encode('utf-8'))>1_000_000:return
    if hashlib.sha256(raw.encode('utf-8')).hexdigest()!=context['evidence_revision']:return
    try:
        from vmodel_contracts import IntentModel,ConOpsDocument
        from pydantic import TypeAdapter
        from vmodel_contracts import StakeholderContext,OperationalScenario,ConceptOption
        evidence=context['evidence']
        if context['decision_purpose']=='semantic_review':
            from vmodel_contracts.semantic_review import SemanticReview
            semantic=SemanticReview.model_validate(evidence['semantic_review'])
            if semantic.session_id!=context['session_id']:return
        elif context['decision_purpose']=='interview_approval':
            IntentModel.model_validate(evidence['intent_model']);ConOpsDocument.model_validate(evidence['conops'])
        else:
            draft=evidence['conops_draft']
            TypeAdapter(list[StakeholderContext]).validate_python(draft['stakeholder_context'])
            TypeAdapter(list[OperationalScenario]).validate_python(draft['operational_scenarios'])
            options=TypeAdapter(list[ConceptOption]).validate_python(draft['concept_options'])
            if len(options)<2:return
            published=control.get('options',[])
            if len(published)!=len(options):return
            if any(str(row.get('value'))!=str(i) or row.get('label')!=option.name
                   for i,(row,option) in enumerate(zip(published,options),1)):return
    except (ValueError,KeyError,TypeError):return

    if context.get('session_id')!=control.get('precondition',{}).get('session_id'):return
    try:store.retain_decision_review(task_id,control['wait_id'],control['revision'],control)
    except ValueError as exc:
        raise HTTPException(409,'Engine decision evidence changed without a new revision; reload a fresh engine decision before answering') from exc

@app.get("/api/tasks/{task_id}/decisions/{wait_id}")
def get_decision_review(task_id:str,wait_id:str,revision:str|None=None,draft:str|None=None,a:auth.Actor=Depends(auth.require_actor)):
    task=get_task(task_id);current=task['control_snapshot'];_retain_decision_control(task_id,current)
    review=store.decision_review(task_id,wait_id,revision)
    if review is None:raise HTTPException(404,{"error":"This decision view is unavailable.","cause":"The identified review evidence was not retained.","next":"Open the current task to find its live decision."})
    live=current.get('owner')=='engine' and current.get('mode')=='waiting_input' and current.get('wait_id')==wait_id and current.get('revision')==review['revision']
    saved=None
    if draft:
        saved=store.decision_draft(task_id,wait_id,review['revision'],draft)
        if saved is None:raise HTTPException(404,{"error":"Saved draft is unavailable.","cause":"It does not belong to this decision revision.","next":"Use the original review link or open the current task."})
    return {'task_id':task_id,'title':task['title'],'review':review,'current':current,'actionable':live,'draft':saved,
            'comments':store.review_comments(task_id,wait_id,review['revision'])}

class DecisionComment(BaseModel):
    revision:str=Field(min_length=1,max_length=240)
    comment:str=Field(min_length=1,max_length=7000)

@app.post("/api/tasks/{task_id}/decisions/{wait_id}/comments")
def comment_decision_review(task_id:str,wait_id:str,body:DecisionComment,a:auth.Actor=Depends(auth.require_actor)):
    review=store.decision_review(task_id,wait_id,body.revision)
    if review is None:raise HTTPException(404,'decision revision not found')
    event=store.add_event(task_id,'artifact_comment',body.comment,phase_key=review.get('phase_key'),
        payload={'wait_id':wait_id,'revision':body.revision,'actor':a.label,'comment':body.comment})
    comment_id=store.add_review_comment(task_id,wait_id,body.revision,a.label,body.comment)
    return {'comment_id':comment_id}

class DecisionDraft(BaseModel):
    semantic_decisions:list[dict]=Field(default_factory=list,max_length=1000)
    founder_inputs:list[dict]=Field(default_factory=list,max_length=1000)
    revision:str=Field(min_length=1,max_length=240)
    choice:str|None=Field(default=None,max_length=200)
    rationale:str=Field(default='',max_length=7000)
    comment:str=Field(default='',max_length=7000)

@app.post("/api/tasks/{task_id}/decisions/{wait_id}/drafts")
def save_decision_review_draft(task_id:str,wait_id:str,body:DecisionDraft,a:auth.Actor=Depends(auth.require_actor)):
    if not store.task(task_id):raise HTTPException(404,'task not found')
    review=store.decision_review(task_id,wait_id,body.revision)
    if review is None:raise HTTPException(404,'decision revision not found')
    if body.choice is not None and body.choice not in [str(o['value']) for o in review.get('options',[])]:raise HTTPException(422,'unknown option')
    draft=store.save_decision_draft(task_id,wait_id,body.revision,{**body.model_dump(),'actor':a.label})
    return {'draft_id':draft,'revision':body.revision}

class BrowserDiagnostics(BaseModel):
    started_at:str|None=None
    ended_at:str|None=None
    url:str=Field(default='',max_length=2000)
    records:list[dict]=Field(default_factory=list,max_length=500)

@app.post("/api/tasks/{task_id}/debug.zip")
def download_debug_zip(task_id:str,body:BrowserDiagnostics,a:auth.Actor=Depends(auth.require_actor)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    if config.CLOUD_TENANTS:raise HTTPException(503,'Provider audit diagnostics are internal-only in cloud mode')
    session=_recorded_engine_session(task_id)
    if not session:raise HTTPException(409,{"error":"No run is linked to this task.","cause":"A task/run-scoped diagnostic export needs a recorded session.","next":"Start a run or select a task with a run."})
    if not __import__('re').fullmatch(r'[A-Za-z0-9._-]+',session) or session in ('.','..'):raise HTTPException(409,'invalid recorded session')
    if len(json.dumps(body.model_dump()).encode())>1_000_000:raise HTTPException(413,'browser diagnostics exceed 1 MB')
    from vmodel_contracts.runtime import artifacts_root
    from .debug_export import diagnostic_zip
    from .observability import LOG_PATH
    root=authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store)
    control=get_task(task_id)['control_snapshot']
    data=diagnostic_zip(root,task_id,session,task['generation'],body.model_dump(),control,store.events(task_id),root/'server-run.log' if config.CLOUD_TENANTS else LOG_PATH,engine_log.default_path())
    from fastapi.responses import Response
    activity("task.export","Debug zip downloaded",task_id=task_id,export="debug_zip",actor=a.label)
    return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{task_id}-debug.zip"'})

@app.get("/api/tasks/{task_id}/execution")
def task_execution(task_id:str,debug:bool=False,a:auth.Actor=Depends(auth.require_actor)):
    """Per-session routing, usage and call diagnostics; raw payloads require debug mode."""
    if not store.task(task_id):raise HTTPException(404,"task not found")
    if config.CLOUD_TENANTS:raise HTTPException(404,"provider diagnostics are internal")
    session=_recorded_engine_session(task_id)
    if not session:return {"session_id":None,"route":None,"usage":[],"calls":[],"raw":[]}
    from vmodel_contracts.runtime import artifacts_root
    root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"
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
    # Preserve provider audit files privately; no debug flag bypass in cloud.
    if config.CLOUD_TENANTS:debug=False
    usage=[] if config.CLOUD_TENANTS else lines(root/"usage.jsonl")
    calls=lines(root/"llm-calls.jsonl")
    summary=[]
    for entry in calls:
        item={key:entry.get(key) for key in ("ts","event","stage","provider","model","attempt","latency_s","usage") if key in entry}
        if config.CLOUD_TENANTS:item.pop("usage",None)
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

def _customer_events(events):
    if not config.CLOUD_TENANTS:return events
    from .customer_projection import without_provider_usage,without_model_pricing
    return without_model_pricing(without_provider_usage(events))

@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(default=0, ge=0)):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    return {"events": _customer_events(store.events(task_id, after))}

def stream_events_timed(store, task_id, cursor, acc):
    """One stream event read, timed. These sync DB reads run ON the event loop: while one
    executes, every in-flight request waits. The accumulated blocked time is reported on
    stream disconnect so loop stalls are measured, not guessed."""
    started=time.monotonic()
    events=store.events(task_id,cursor)
    blocked=int((time.monotonic()-started)*1000)
    acc['cycles']+=1;acc['blocked_ms']+=blocked;acc['max_ms']=max(acc['max_ms'],blocked)
    return events


# The stream waits on writes through this process (event_bus) and otherwise reads only
# on the safety cadence - no steady-state database polling. Client heartbeats keep the
# SSE connection alive for free between reads.
STREAM_HEARTBEAT_SECONDS = 15.0
STREAM_SAFETY_SECONDS = 60.0


def loop_lag_ms(expected, actual):
    """How late the loop woke for a scheduled tick: positive means something blocked it."""
    return max(0,int((actual-expected)*1000))


import logging as _logging

from . import event_bus


async def _loop_lag_monitor():
    """Ticks once a second and names stalls: a request that 'hangs' while the loop is blocked
    by sync work (stream DB reads, serialization) is otherwise indistinguishable from a slow
    database. Rate-limited; routine ticks are not logged."""
    import asyncio as _asyncio
    from .observability import event as _activity
    expected=time.monotonic()+1.0;last_report=0.0
    while True:
        await _asyncio.sleep(1.0)
        now=time.monotonic();lag=loop_lag_ms(expected,now);expected=now+1.0
        if lag>=500 and now-last_report>=60:
            last_report=now
            _activity("loop.lag","Event loop stall",level=_logging.WARNING,lag_ms=lag)


@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str, request: Request):
    # Every database read for this endpoint runs in the threadpool: the reads are
    # synchronous and remote, and on the event loop each one stalls every request.
    from starlette.concurrency import run_in_threadpool
    if not await run_in_threadpool(store.task,task_id): raise HTTPException(404, "task not found")
    stop=asyncio.Event();_stream_shutdown_events.add(stop)
    async def generate():
        loop_block={'cycles':0,'blocked_ms':0,'max_ms':0}
        connected_at=time.monotonic()
        try:
            seen_version = event_bus.version(task_id)
            existing = await run_in_threadpool(stream_events_timed,store,task_id,0,loop_block)
            activity("stream.connect","Progress stream connected",task_id=task_id,backlog=len(existing))
            cursor = existing[-1]["id"] if existing else 0
            since_read = 0.0
            yield f"event: heartbeat\ndata: {json.dumps({'task_id': task_id})}\n\n"
            while not stop.is_set() and not await request.is_disconnected():
                woke = await run_in_threadpool(event_bus.wait,task_id,STREAM_HEARTBEAT_SECONDS,stop,seen_version)
                if stop.is_set():break
                since_read += STREAM_HEARTBEAT_SECONDS
                if woke or since_read >= STREAM_SAFETY_SECONDS:
                    since_read = 0.0
                    seen_version = event_bus.version(task_id)
                    t_events=time.monotonic()
                    events = await run_in_threadpool(stream_events_timed,store,task_id,cursor,loop_block)
                    if time.monotonic()-t_events>2:
                        activity("stream.events.slow","Progress stream event read slow",task_id=task_id,dur_ms=int((time.monotonic()-t_events)*1000))
                    for event in _customer_events(events):
                        cursor = event["id"]
                        yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                yield f"event: heartbeat\ndata: {json.dumps({'task_id': task_id})}\n\n"

        finally:
            _stream_shutdown_events.discard(stop)
            from . import request_costs
            request_costs.record_stream((time.monotonic()-connected_at)*1000,loop_block['blocked_ms'],loop_block['max_ms'])
            activity("stream.disconnect","Progress stream closed",task_id=task_id,reason="client" if await request.is_disconnected() else "server",
                     loop_blocked_ms=loop_block['blocked_ms'],db_cycles=loop_block['cycles'],max_cycle_ms=loop_block['max_ms'])
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

class FailedNeedsRetryRequest(BaseModel):
    confirm: bool = False
    model_route: Literal["ollama","deepseek"]|None = None
    model: str|None = Field(default=None,min_length=1,max_length=160)

@app.post("/api/tasks/{task_id}/retry-needs")
async def retry_failed_needs(task_id:str,body:FailedNeedsRetryRequest,a:auth.Actor=Depends(auth.require_run_start)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    if config.CLOUD_TENANTS and body.confirm and not cloud_execution_admitted():
        raise HTTPException(503,"Cloud admission and lifecycle validation pending")
    phases={phase["phase_key"]:phase["state"] for phase in task["phases"]}
    session=store.engine_session(task_id)
    eligible=(task["state"]=="failed" and task["active_phase"]=="needs" and phases["needs"]=="failed"
              and all(phases[key] in ("approved","done") for key in ("intent","conops"))
              and all(phases[key]=="pending" for key in ("srs","architecture","specs","tests","validation"))
              and session and runner.work_state(task_id) is None and task_id not in runner.processes)
    def reject(reason: str, next_step: str) -> None:
        if body.confirm:
            store.audit(a.label,"phase.retry_rejected",task_id,{"phase":"needs","reason":reason,"next":next_step})
            store.add_event(task_id,"retry_rejected",f"Stakeholder needs retry was not started: {reason}",phase_key="needs",
                            payload={"reason":reason,"next":next_step})
        activity("phase.retry.rejected","Stakeholder needs retry blocked",task_id=task_id,reason=reason,confirmed=body.confirm)
        raise HTTPException(409,{"error":"Stakeholder needs retry was not started.","cause":reason,"next":next_step})
    if not eligible:reject("The task is no longer an eligible failed Needs phase, or work is already live.","Refresh the task and review its current phase before trying again.")
    from vmodel_contracts.runtime import artifacts_root
    from vmodel_contracts.interview import IntentModel
    from vmodel_contracts.conops import ConOpsDocument
    interview=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"
    try:
        intent=IntentModel.model_validate_json((interview/"intent_model.json").read_text())
        conops=ConOpsDocument.model_validate_json((interview/"conops.json").read_text())
        if intent.session_id!=session or conops.session_id!=session:raise ValueError("interview artifact session IDs do not match")
    except (OSError,ValueError) as exc:
        reject(f"The linked interview artifacts are missing or invalid: {exc}","Restore the named session artifacts before retrying Needs.")
    from . import model_routes as downstream_routes
    try: default,default_source=downstream_routes.task_default_model(store,task_id,"needs")
    except ValueError: default,default_source=None,"selection required"
    default_route,_,default_model=(default or "").partition(":")
    route=body.model_route or default_route
    model=body.model or (default_model if route==default_route else None)
    if not route or not model:
        if body.confirm:reject("An exact model selection is required for this session.","Choose a provider and model in the retry dialog.")
        effective=None
    else:
        try:effective=downstream_routes.route_value(route,model)
        except ValueError:reject("The selected downstream model is unsupported.","Choose a supported provider and model before retrying.")
    if body.confirm and (not body.model_route or not body.model):
        reject("The retry model was not explicitly confirmed.","Choose a provider and model in the retry dialog.")
    spec=interview.parent/"spec"
    # A first Needs attempt can die on import before LangGraph writes a
    # checkpoint. --from needs rejects that state. An existing checkpoint
    # should be resumed by a normal launch, preserving completed work.
    checkpoint=(spec/"checkpoints.sqlite").is_file() and (spec/"checkpoints.sqlite").stat().st_size>0
    latest_failure=next((event for event in reversed(store.events(task_id)) if event["phase_key"]=="needs" and event["kind"]=="error"),None)
    if not body.confirm:
        return {"status":"preview","phase":"needs","session_id":session,"retains":["intent","conops"],"model_route":effective,"default_model":default,"default_source":default_source,"checkpoint":checkpoint,"mode":"resume" if checkpoint else "start",
                "last_failure":latest_failure["message"] if latest_failure else next(phase["detail"] for phase in task["phases"] if phase["phase_key"]=="needs"),
                "message":f"Retry Stakeholder needs from the approved interview. Choose the downstream model; this may make new model calls and incur cost. The interview is not reopened."}
    if config.CLOUD_TENANTS:
        job_id=store.prepare_phase_retry(task_id,"needs",a.label,effective,checkpoint)
        if not job_id:reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
        await _run_failed_phase_retry(task_id,"needs","Stakeholder needs",effective,checkpoint,queued_job_id=job_id)
        return {"status":"started","phase":"needs","session_id":session,"job_id":job_id}
    if not store.claim_failed_needs_retry(task_id,a.label):reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
    store.set_model_default(task_id,effective)
    store.add_event(task_id,"model_route",f"Needs retry model selected: {effective}",phase_key="needs",payload={"route":route,"resolved":effective,"source":"explicit_retry_choice"})
    store.audit(a.label,"phase.retry_model",task_id,{"phase":"needs","model":effective,"checkpoint":checkpoint})
    activity("phase.retry","Founder confirmed retry of Stakeholder needs",task_id=task_id,session_id=session,actor=a.label,model_route=effective,checkpoint=checkpoint)
    spawn(_run_failed_phase_retry(task_id,"needs","Stakeholder needs",effective,checkpoint))
    return {"status":"started","phase":"needs","session_id":session}

async def _dispatch_failed_retry(task_id:str,phase:str,model_route:str,checkpoint:bool,*,queued_job_id=None)->None:
    options={"mode":ExecutionMode.RESUME if checkpoint else ExecutionMode.START,"model_route":model_route}
    if config.CLOUD_TENANTS:
        from .tenant_jobs import dispatch_phase
        await dispatch_phase(store,orchestrator,task_id,phase,queued_job_id=queued_job_id,**options)
    else:await orchestrator.execute_phase(task_id,phase,**options)

class FailedPhaseRetryRequest(BaseModel):
    phase: str
    confirm: bool = False
    model_route: Literal["ollama","deepseek"]|None = None
    model: str|None = Field(default=None,min_length=1,max_length=160)

@app.post("/api/tasks/{task_id}/retry-phase")
async def retry_failed_phase(task_id:str,body:FailedPhaseRetryRequest,a:auth.Actor=Depends(auth.require_run_start)):
    """Resume a failed generator phase after Stakeholder needs from its checkpoint.
    Stakeholder needs keeps its own retry endpoint. Nothing upstream or downstream is reset."""
    from .phases import PHASE_BY_KEY, PHASE_KEYS
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    if config.CLOUD_TENANTS and body.confirm and not cloud_execution_admitted():
        raise HTTPException(503,"Cloud admission and lifecycle validation pending")
    definition=PHASE_BY_KEY.get(body.phase)
    if not definition or definition.executor!="generator" or body.phase=="needs":
        raise HTTPException(409,{"error":"That phase cannot be retried this way.","cause":f"'{body.phase}' is not a failed generator phase after Stakeholder needs.","next":"Use the phase's own retry, or Rerun stage."})
    label=definition.label
    phases={phase["phase_key"]:phase["state"] for phase in task["phases"]}
    index=PHASE_KEYS.index(body.phase);session=store.engine_session(task_id)
    eligible=(task["state"]=="failed" and task["active_phase"]==body.phase and phases.get(body.phase)=="failed"
              and all(phases.get(key) in ("approved","done") for key in PHASE_KEYS[:index])
              and all(phases.get(key) in ("pending","failed") for key in PHASE_KEYS[index+1:])
              and session and runner.work_state(task_id) is None and task_id not in runner.processes)
    def reject(reason: str, next_step: str) -> None:
        if body.confirm:
            store.audit(a.label,"phase.retry_rejected",task_id,{"phase":body.phase,"reason":reason,"next":next_step})
            store.add_event(task_id,"retry_rejected",f"{label} retry was not started: {reason}",phase_key=body.phase,payload={"reason":reason,"next":next_step})
        activity("phase.retry.rejected",f"{label} retry blocked",task_id=task_id,reason=reason,confirmed=body.confirm)
        raise HTTPException(409,{"error":f"{label} retry was not started.","cause":reason,"next":next_step})
    if not eligible:reject(f"The task is no longer an eligible failed {label} phase, or work is already live.","Refresh the task and review its current phase before trying again.")
    from vmodel_contracts.runtime import artifacts_root
    from vmodel_contracts.interview import IntentModel
    from vmodel_contracts.conops import ConOpsDocument
    interview=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"
    try:
        intent=IntentModel.model_validate_json((interview/"intent_model.json").read_text())
        conops=ConOpsDocument.model_validate_json((interview/"conops.json").read_text())
        if intent.session_id!=session or conops.session_id!=session:raise ValueError("interview artifact session IDs do not match")
    except (OSError,ValueError) as exc:
        reject(f"The linked interview artifacts are missing or invalid: {exc}","Restore the named session artifacts before retrying.")
    from . import model_routes as downstream_routes
    try: default,default_source=downstream_routes.task_default_model(store,task_id,definition.generator_stage)
    except ValueError: default,default_source=None,"selection required"
    default_route,_,default_model=(default or "").partition(":")
    route=body.model_route or default_route
    model=body.model or (default_model if route==default_route else None)
    if not route or not model:
        if body.confirm:reject("An exact model selection is required for this session.","Choose a provider and model in the retry dialog.")
        effective=None
    else:
        try:effective=downstream_routes.route_value(route,model)
        except ValueError:reject("The selected downstream model is unsupported.","Choose a supported provider and model before retrying.")
    if body.confirm and (not body.model_route or not body.model):
        reject("The retry model was not explicitly confirmed.","Choose a provider and model in the retry dialog.")
    spec=interview.parent/"spec"
    checkpoint=(spec/"checkpoints.sqlite").is_file() and (spec/"checkpoints.sqlite").stat().st_size>0
    latest_failure=next((event for event in reversed(store.events(task_id)) if event["phase_key"]==body.phase and event["kind"]=="error"),None)
    if not body.confirm:
        return {"status":"preview","phase":body.phase,"label":label,"session_id":session,"retains":list(PHASE_KEYS[:index]),"model_route":effective,"default_model":default,"default_source":default_source,"checkpoint":checkpoint,"mode":"resume" if checkpoint else "start",
                "last_failure":latest_failure["message"] if latest_failure else next(phase["detail"] for phase in task["phases"] if phase["phase_key"]==body.phase),
                "message":f"Retry {label} from the approved earlier phases. Choose the downstream model; this may make new model calls and incur cost. Nothing earlier is reopened."}
    if config.CLOUD_TENANTS:
        job_id=store.prepare_phase_retry(task_id,body.phase,a.label,effective,checkpoint)
        if not job_id:reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
        await _run_failed_phase_retry(task_id,body.phase,label,effective,checkpoint,queued_job_id=job_id)
        return {"status":"started","phase":body.phase,"session_id":session,"job_id":job_id}
    if not store.claim_failed_phase_retry(task_id,body.phase,a.label):reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
    store.set_model_default(task_id,effective)
    store.add_event(task_id,"model_route",f"{label} retry model selected: {effective}",phase_key=body.phase,payload={"route":route,"resolved":effective,"source":"explicit_retry_choice"})
    store.audit(a.label,"phase.retry_model",task_id,{"phase":body.phase,"model":effective,"checkpoint":checkpoint})
    activity("phase.retry",f"Founder confirmed retry of {label}",task_id=task_id,session_id=session,actor=a.label,model_route=effective,checkpoint=checkpoint)
    spawn(_run_failed_phase_retry(task_id,body.phase,label,effective,checkpoint))
    return {"status":"started","phase":body.phase,"session_id":session}

async def _run_failed_phase_retry(task_id:str,phase:str,label:str,model_route:str,checkpoint:bool,*,queued_job_id=None)->None:
    try:await _dispatch_failed_retry(task_id,phase,model_route,checkpoint,queued_job_id=queued_job_id)
    except Exception as exc:
        message=f"{label} retry stopped: {type(exc).__name__}: {exc}"
        store.update_phase(task_id,phase,"failed",detail=message)
        store.add_event(task_id,"error",message,phase_key=phase,payload={"cause":str(exc),"retry":True})
        activity("phase.retry.failed",f"{label} retry did not launch",task_id=task_id,error=message)

class StartNextPhaseRequest(BaseModel):
    confirm: bool = False

_phase_jobs=set()

@app.post("/api/tasks/{task_id}/start-next-phase")
async def start_next_phase(task_id:str,body:StartNextPhaseRequest,a:auth.Actor=Depends(auth.require_run_start)):
    """Start the phase after the interview for a cloud account. Only the owner's click starts it, never the finish of
    the interview. The preview spends nothing. The only ceiling is the owner's credits; each provider call is then
    reserved and checked on its own by the money gateway."""
    if not config.CLOUD_TENANTS:raise HTTPException(404,"not available")
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    from . import phase_start
    from .phases import PHASE_BY_KEY
    if not store.engine_session(task_id) or not phase_start.eligible(task):
        raise HTTPException(409,{"error":"This phase cannot be started now.","cause":"The interview is not finished, or this phase has already started.","next":"Refresh the page."})
    phase=phase_start.PHASE;label=PHASE_BY_KEY[phase].label
    if body.confirm and not cloud_execution_admitted():raise HTTPException(503,"Cloud admission and lifecycle validation pending")
    from .pricing import retail_rates
    check=phase_start.credit_check(store.own_credit_balance(retail_rates().usd_per_credit).get("credits"),config.NEXT_PHASE_MIN_CREDITS)
    if not body.confirm:return {"status":"preview","phase":phase,"label":label,**check}
    if not check["can_start"]:
        store.audit(a.label,"phase.start_refused",task_id,{"phase":phase,"reason":"credits"})
        activity("phase.start.credit_refused","Next phase refused: not enough credits",task_id=task_id,phase=phase,actor=a.label)
        raise HTTPException(409,{"error":"Not enough credits to start this phase.","cause":check["message"],"next":"Add credits, then start it."})
    model=task.get("model_default") or ""
    route,sep,pinned=model.partition(":")
    if not sep or not pinned or route not in local_route.managed_routes():
        activity("phase.start.no_model","Next phase has no recorded model",task_id=task_id,model=model[:60])
        raise HTTPException(409,{"error":"This phase cannot be started now.","cause":"The task has no recorded model for it.","next":"Open Debug, copy everything and send it."})
    job_id=store.prepare_next_phase(task_id,phase,a.label,model)
    if not job_id:raise HTTPException(409,{"error":"This phase was already started.","cause":"A start for it is already recorded.","next":"Refresh the page."})
    activity("phase.start.queued","Next phase job queued",task_id=task_id,phase=phase,job_id=job_id,actor=a.label)
    runner_task=spawn(_run_next_phase(task_id,phase,label,job_id))
    _phase_jobs.add(runner_task);runner_task.add_done_callback(_phase_jobs.discard)
    return {"status":"started","phase":phase,"job_id":job_id}

async def _run_next_phase(task_id:str,phase:str,label:str,job_id:str)->None:
    try:
        from .tenant_jobs import dispatch_phase
        await dispatch_phase(store,orchestrator,task_id,phase,queued_job_id=job_id)
    except Exception as exc:
        from .user_messages import scrub_failure_text
        message=scrub_failure_text(f"{label} stopped: {type(exc).__name__}: {exc}")
        store.update_phase(task_id,phase,"failed",detail=message)
        store.add_event(task_id,"error",message,phase_key=phase,payload={"cause":message,"first_start":True})
        activity("phase.start.failed",f"{label} did not run",task_id=task_id,error=f"{type(exc).__name__}: {str(exc)[:300]}")

class RerunRequest(BaseModel):
    from_phase: str
    confirm: bool = False

def _rerun_control():
    if not config.CLOUD_TENANTS:return {"available":True}
    return {"available":False,"reason":"Tenant stage rerun is unavailable: new-generation session/checkpoint recovery is not implemented."}

@app.post("/api/tasks/{task_id}/rerun")
def rerun(task_id: str, req: RerunRequest, request: Request, a: auth.Actor = Depends(auth.require_run_start)):
    task=store.task(task_id)
    if not task: raise HTTPException(404, "task not found")
    try:missing=store.rerun_prerequisite(task_id,req.from_phase)
    except KeyError:
        activity("task.rerun.refused","Rerun refused: unknown stage",task_id=task_id,actor=a.label,from_phase=req.from_phase,reason="unknown_stage")
        raise HTTPException(409,{"error":f"Cannot rerun from unknown stage '{req.from_phase}'.","cause":"That stage is not part of this task.","next":"Choose one of the stages shown in the V-process."})
    if missing:
        activity("task.rerun.refused","Rerun refused: prerequisite incomplete",task_id=task_id,actor=a.label,from_phase=req.from_phase,reason="prerequisite")
        raise HTTPException(409,{"error":f"Cannot rerun from {req.from_phase}: prerequisite '{missing['label']}' is not complete.","cause":"Stages are sequential; rerunning a downstream stage without its upstream result would produce ungrounded output.","next":f"Run or rerun '{missing['label']}' first, then retry {req.from_phase}."})
    if config.CLOUD_TENANTS:
        raise HTTPException(409,{"code":"tenant_rerun_unavailable",
            "error":"Stage rerun is unavailable for tenant tasks.",
            "cause":_rerun_control()["reason"],
            "next":"No work changed. Check the task Audit. Use a supported failed-phase Retry if shown; otherwise create a new task to start again (new model calls may cost credits)."})
    if not req.confirm:
        downstream = [p["phase_key"] for p in task["phases"] if p["position"] >= next(p["position"] for p in task["phases"] if p["phase_key"] == req.from_phase)]
        return {"status": "preview", "from_phase": req.from_phase, "policy": "archive", "downstream": downstream,
                "message": "Downstream artifacts will be marked stale and retained under their generation. Their gates will require re-approval."}
    try: generation = store.prepare_rerun(task_id, req.from_phase, "archive", a.label)
    except (ValueError, KeyError) as e:
        activity("task.rerun.refused","Rerun refused: invalid phase",task_id=task_id,actor=a.label,from_phase=req.from_phase,reason="invalid_phase")
        raise HTTPException(409, {"error": str(e), "cause": "The selected phase is invalid.",
                                  "next": "Choose one of the phases shown on this task."})
    activity("task.rerun","Rerun prepared",task_id=task_id,actor=a.label,from_phase=req.from_phase,generation=generation)
    store.add_event(task_id, "rerun", f"Prepared generation {generation} from {req.from_phase}", phase_key=req.from_phase)
    return {"status": "prepared", "generation": generation, "policy": "archive"}

def _is_credit_event(event:dict)->bool:
    # Events written before the credit rescale carry no scale marker and sit on the old unit scale.
    return ((event.get("payload") or {}).get("rates") or {}).get("scale")=="credits"

def _task_cost(task_id:str)->dict:
    """Running customer price: credits and USD only. Priced phases come from their
    price events; model tokens recorded in the usage files but not yet covered by an
    event (the phase is still running) and all interview tokens are added at the token
    rate, so the total grows as calls land. Interview calls carry no process-time part."""
    if hasattr(store,'task_retail_totals'):
        exact=store.task_retail_totals(task_id)
        if exact is not None:return exact
    from .pricing import usage_tokens,token_credits,credits_to_usd
    latest={}
    for event in store.events(task_id):
        if event.get("kind")=="price":latest[event.get("phase_key")]=event
    session=_recorded_engine_session(task_id);interview_tokens=spec_tokens=0
    if session:
        from vmodel_contracts.runtime import artifacts_root
        root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)
        if config.CLOUD_TENANTS:
            with session_reader(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store):
                interview_tokens=usage_tokens(root/'interview'/'usage.jsonl');spec_tokens=usage_tokens(root/'spec'/'usage.jsonl')
        else:interview_tokens=usage_tokens(root/"interview"/"usage.jsonl");spec_tokens=usage_tokens(root/"spec"/"usage.jsonl")
    counted=[e for e in latest.values() if _is_credit_event(e) and not (interview_tokens and e.get("phase_key") in ("intent","conops"))]
    event_price=sum(float((e.get("payload") or {}).get("price") or 0) for e in counted)
    event_tokens=sum(int((e.get("payload") or {}).get("tokens") or 0) for e in counted if e.get("phase_key") not in ("intent","conops"))
    price=event_price+token_credits(interview_tokens)+token_credits(max(0,spec_tokens-event_tokens))
    result={"price":round(price,6),"price_usd":round(credits_to_usd(price),6)}
    if session and not config.CLOUD_TENANTS:
        from .pricing import usage_evidence
        result['evidence']=usage_evidence(root)
    return result

@app.get("/api/tasks/{task_id}/cost")
def task_cost(task_id:str,request:Request):
    if not store.task(task_id):raise HTTPException(404,"task not found")
    session=_recorded_engine_session(task_id)
    if session and not config.CLOUD_TENANTS:
        from vmodel_contracts.runtime import artifacts_root
        from .pricing import usage_evidence
        evidence=usage_evidence(authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store))
        if not evidence['complete']:return {'price':None,'price_usd':None,'evidence':evidence}
        try:
            from vmodel_contracts.runtime import artifacts_root
            from .pricing import write_cost_json
            write_cost_json(authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store))
        except (OSError,ValueError) as error:
            activity('local.cost_rollup.unavailable','Local cost evidence rollup was not updated',level=logging.WARNING,category=type(error).__name__)
    return _task_cost(task_id)

@app.get("/api/tasks/{task_id}/cost-phases")
def task_cost_phases(task_id:str):
    # Credits per phase, for the header tooltip. Customer-safe: phase names and credits only. Local mode has no per-phase ledger.
    if not store.task(task_id):raise HTTPException(404,"task not found")
    if not hasattr(store,'task_phase_credits'):return {'phases':[]}
    return {'phases':store.task_phase_credits(task_id)}

@app.get("/api/sessions/{session_id}/cost")
def session_cost(session_id:str):
    # Discovery validates the path and ties the read to an existing session.
    try:_session_bundle(session_id)
    except (FileNotFoundError,ValueError):raise HTTPException(404,"session bundle not found")
    linked=[task for task in store.list_tasks() if _recorded_engine_session(task["task_id"])==session_id]
    if len(linked)>1:raise HTTPException(409,"session has multiple task bindings; cost cannot be selected safely")
    if linked:return _task_cost(linked[0]["task_id"])
    from .pricing import usage_tokens,token_credits,credits_to_usd,_rows
    root=authorized_session(bundles.root(),session_id,store)
    paths=[root/area/"usage.jsonl" for area in ("interview","spec")]
    if not any("prompt_tokens" in row and "completion_tokens" in row for row in _rows(paths[0])):raise HTTPException(404,"session usage unavailable")
    if (root/"spec").is_dir() and any((root/"spec").iterdir()):raise HTTPException(404,"complete session cost unavailable without recorded task pricing")
    price=token_credits(sum(usage_tokens(path) for path in paths))
    return {"price":round(price,6),"price_usd":round(credits_to_usd(price),6)}


@app.get("/api/tasks/{task_id}/audit")
def task_audit(task_id: str, request: Request):
    if not store.task(task_id): raise HTTPException(404, "task not found")
    priced_by_phase={}
    for event in store.events(task_id):
        if event.get("kind")=="price":priced_by_phase[event.get("phase_key")]=event
    priced=list(priced_by_phase.values())
    session=_recorded_engine_session(task_id)
    interview_usage=[]
    if session:
        from vmodel_contracts.runtime import artifacts_root
        path=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"/"usage.jsonl"
        if path.is_file():
            from .pricing import _rows
            if config.CLOUD_TENANTS:
                with session_reader(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store):interview_usage=_rows(path)
            else:interview_usage=_rows(path)
    # The HTTP interview does not run through ProcessRunner, so it has no
    # phase-price event. Read its per-call records, but do not double-count
    # historical subprocess prices if both forms exist for an older task.
    priced_tokens=sum(int((event.get("payload") or {}).get("tokens") or 0) for event in priced
                      if not (interview_usage and event.get("phase_key") in ("intent","conops")))
    segments=_model_usage_segments(interview_usage)
    interview_tokens=sum(int(row.get("prompt_tokens") or 0)+int(row.get("completion_tokens") or 0)
                         for row in interview_usage if isinstance(row,dict))
    totals={"tokens":priced_tokens+interview_tokens,
            "price":round(sum(float((event.get("payload") or {}).get("price") or 0) for event in priced
                              if _is_credit_event(event) and not (interview_usage and event.get("phase_key") in ("intent","conops"))),6),
            "phases_priced":len(priced),"legacy_priced_phases":sum(1 for event in priced if not _is_credit_event(event)),"interview_calls":sum(x["calls"] for x in segments),
            "interview_cost_usd":round(sum(x["cost_usd"] for x in segments),6),
            "interview_unpriced_calls":sum(x["unpriced_calls"] for x in segments)}
    counted=[event for event in priced if _is_credit_event(event) and not (interview_usage and event.get("phase_key") in ("intent","conops"))]
    totals["price_usd"]=round(sum(float((event.get("payload") or {}).get("price_usd") or 0) for event in counted),6)
    totals["cost_usd"]=round(sum(float((event.get("payload") or {}).get("cost_usd") or 0) for event in counted)+totals["interview_cost_usd"],6)
    live=_task_cost(task_id);totals["price"]=live["price"];totals["price_usd"]=live["price_usd"]
    totals["unpriced_calls"]=sum(int((event.get("payload") or {}).get("unpriced_calls") or 0) for event in counted)+totals["interview_unpriced_calls"]
    entries=store.audit_entries(task_id)
    if config.CLOUD_TENANTS:
        from .customer_projection import audit_credits_only
        entries=audit_credits_only(_customer_events(entries))
        totals={'price':totals['price']}
    return {"actor_mode": auth.actor(request).label, "entries": entries,"totals":totals}

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
@app.get("/api/tasks/{task_id}/generator-default/{phase}")
def generator_default(task_id:str,phase:str,a:auth.Actor=Depends(auth.require_actor)):
    from .phases import PHASE_BY_KEY
    if not store.task(task_id):raise HTTPException(404,"task not found")
    definition=PHASE_BY_KEY.get(phase)
    if not definition or definition.executor!="generator":raise HTTPException(404,"generator phase not found")
    try: model,source=model_route_lib.task_default_model(store,task_id,definition.generator_stage)
    except ValueError as exc:return {"phase":phase,"model":None,"source":"selection required","reason":str(exc)}
    return {"phase":phase,"model":model,"source":source}

@app.post("/api/tasks/{task_id}/gates/{gate_key}/toggle")
def toggle_gate(task_id:str,gate_key:str,req:GateToggle,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    try:return store.set_gate_open(task_id,gate_key,req.is_open,a.label)
    except KeyError:raise HTTPException(404,"gate not found")

def _srs_semantic_path(task_id:str)->Path:
    session=_recorded_engine_session(task_id)
    if not session or not __import__('re').fullmatch(r'[A-Za-z0-9._-]+',session) or session in ('.','..'):
        raise HTTPException(409,'task has no valid session')
    from vmodel_contracts.runtime import artifacts_root
    return authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store)/'spec'/'srs.semantic-review.json'

def _stack_decisions_path(task_id:str)->Path:
    return _srs_semantic_path(task_id).with_name('architecture.decisions.json')

def _read_stack_decisions(task_id:str):
    """The architecture gate's open research_required slots (written by spec-generator), or None."""
    if not _recorded_engine_session(task_id):return None
    path=_stack_decisions_path(task_id)
    if not path.is_file() or path.is_symlink():return None
    try:data=json.loads(path.read_text())
    except ValueError:raise HTTPException(409,'architecture decisions file is unreadable')
    if not isinstance(data,dict) or not isinstance(data.get('slots'),list) or not isinstance(data.get('revision'),str):
        raise HTTPException(409,'architecture decisions file is malformed')
    return data

def _open_stack_slots(data)->list[str]:
    return [s['key'] for s in (data or {}).get('slots',[]) if not s.get('founder_decision') and not s.get('technical_choice')]

_LINEAGE_PHASE={'specs':'items'}.get  # lineage speaks generator stage names (restore mapping)

def _lineage_record_root(task_id:str):
    """The session's spec dir when this host holds the session artifacts, else None."""
    if not _recorded_engine_session(task_id):return None
    try:return _srs_semantic_path(task_id).parent
    except HTTPException:return None

def _lineage_module_or_none(spec):
    """The generator owns the authority record's format; these decision-time rebinds
    keep it in step with founder mutations. A host holding NO record (spec/lineage.json
    absent) has nothing to maintain: decisions behave exactly as before binding existed,
    and a later restore rebuilds the record from the published manifest. A host WITH a
    record but WITHOUT spec-generator must never let record and bytes drift silently:
    refuse the decision with a clear cause instead of a ModuleNotFoundError stack."""
    try:
        from spec_generator import lineage
        return lineage
    except ImportError as exc:
        if (spec / 'lineage.json').is_file():
            raise ValueError(
                'this session has an authority record (spec/lineage.json) but spec-generator '
                'is not importable on this host, so the decision cannot be bound to the record. '
                'Decide this gate on a host with the full runtime image (or restore the session '
                'there) - the decision was NOT recorded.') from exc
        return None

def _lineage_decision_preflight(task_id:str)->None:
    # Refuse EARLY - before any artifact, manifest or decision-record mutation - when
    # this host holds the session authority record but cannot maintain it. The rebind
    # calls later in the endpoint re-check; this ordering guarantee means a refused
    # decision changes nothing anywhere.
    spec=_lineage_record_root(task_id)
    if spec is None:return
    try:_lineage_module_or_none(spec)
    except ValueError as exc:raise HTTPException(409,str(exc))

def _lineage_gate_settled(task_id:str,phase_key:str)->None:
    """Gate approval flips the phase's bindings to settled: from here a stage commit
    may not replace those bytes without an explicit rewind (--from or a gate redo)."""
    spec=_lineage_record_root(task_id)
    if spec is None:return
    lineage=_lineage_module_or_none(spec)
    if lineage is None:return
    lineage.mark_settled(spec,_LINEAGE_PHASE(phase_key,phase_key))

def _lineage_gate_redo_invalidate(task_id:str,phase_key:str)->None:
    """A gate redo is the founder's explicit rewind: drop bindings for the redone
    phase and everything downstream so the regeneration can re-bind; without this
    the settled-overwrite refusal would fail a sanctioned redo closed."""
    spec=_lineage_record_root(task_id)
    if spec is None:return
    lineage=_lineage_module_or_none(spec)
    if lineage is None:return
    order=['needs','srs','architecture','specs','tests','validation']
    if phase_key not in order:return
    lineage.invalidate_from(spec,{_LINEAGE_PHASE(k,k) for k in order[order.index(phase_key):]})

def _lineage_rebind_decision(task_id:str,phase_key:str,names)->None:
    """Re-bind delivery-side founder-decision mutations (semantic item decisions,
    stack overrides, applied defaults) so generator resume verification admits the
    new bytes. Provisional: the gate is still open; approval flips to settled."""
    spec=_lineage_record_root(task_id)
    if spec is None:return
    lineage=_lineage_module_or_none(spec)
    if lineage is None:return
    task=store.task(task_id)
    phase=_LINEAGE_PHASE(phase_key,phase_key)
    for name in names:
        path=spec/name
        if not path.is_file():continue
        lineage.bind(spec,task_id=task_id,session_id=task.get('session_id') or '',
                     generation=task['generation'],phase=phase,name=name,
                     sha256=lineage.sha256_file(path),source='founder-decision',settled=False)

def _guard_task_file_operation(function):
    # Sync endpoints/helpers only. Never hold a DB transaction across async
    # provider dispatch or gate continuation. Local adapter is unchanged.
    from functools import wraps
    @wraps(function)
    def guarded(*args,**kwargs):
        if not config.CLOUD_TENANTS:return function(*args,**kwargs)
        with store._task_operation_guard('task.gate.decide'):
            return function(*args,**kwargs)
    return guarded


@_guard_task_file_operation
def _use_architecture_defaults(task_id: str):
    """Persist only the defaults actually in the canonical reviewed artifact.

    Companion first is the durable continuation record; failures stop approval.
    An interrupted write is repaired from that same record on resume.
    """
    data = _read_stack_decisions(task_id)
    if not data:
        return
    artifact = _task_artifact(task_id, 'architecture', 'architecture.json')
    full = json.loads(artifact.read_text())
    from vmodel_contracts.architecture import ArchitectureOverview
    ArchitectureOverview.model_validate(full)
    entries = {e['key']: e for e in full.get('stack_entry_dispositions') or []}
    slots = []
    for slot in data['slots']:
        slot = dict(slot)
        entry = entries.get(slot['key'])
        if not entry:
            raise ValueError('technical-choice companion does not match the architecture')
        if slot.get('technical_choice') != entry.get('technical_choice') or slot.get('founder_decision') != entry.get('founder_decision'):
            raise ValueError('technical-choice companion does not match the reviewed architecture')
        framing = entry.get('technical_choice')
        if framing and not entry.get('founder_decision'):
            selection = {'value': framing['default_value'], 'source': 'architecture_default'}
            entry['system_selection'] = selection
            slot['system_selection'] = selection
        slots.append(slot)
    ArchitectureOverview.model_validate(full)
    companion = {**data, 'slots': slots}
    path = _stack_decisions_path(task_id)
    temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(companion, indent=2)); temporary.replace(path)
    temporary = artifact.with_suffix('.tmp'); temporary.write_text(json.dumps(full, indent=2)); temporary.replace(artifact)
    artifact.with_suffix('.md').unlink(missing_ok=True)
    # Applied defaults are durable decision data; republish so the manifest
    # matches local bytes and a later restore does not refuse the batch.
    from .artifact_publication import publish_committed_session
    publish_committed_session(store, task_id, path.parent.parent, phase_key='architecture')
    _lineage_rebind_decision(task_id, 'architecture', ('architecture.json', 'architecture.decisions.json'))


def _gate_review(task_id:str,gate_key:str)->dict:
    task=get_task(task_id)
    gate=next((g for g in task['gates'] if g['gate_key']==gate_key),None)
    if not gate:raise HTTPException(404,'gate not found')
    artifacts=[a for a in task.get('artifacts',[]) if a['phase_key']==gate['after_phase'] and not a.get('stale')]
    documents=[]
    for artifact in artifacts:
        try:documents.append({'name':artifact['name'],'view':task_artifact_view(task_id,gate['after_phase'],artifact['name'])})
        except HTTPException as exc:documents.append({'name':artifact['name'],'error':str(exc.detail)})
    semantic=None
    if gate['after_phase']=='srs' and _recorded_engine_session(task_id):
        path=_srs_semantic_path(task_id)
        if path.is_file():
            from vmodel_contracts.semantic_review import SemanticReview
            semantic=SemanticReview.model_validate_json(path.read_text()).model_dump(mode='json')
            if semantic['session_id']!=_recorded_engine_session(task_id):raise HTTPException(409,'semantic review belongs to another session')
    stack_decisions=_read_stack_decisions(task_id) if gate['after_phase']=='architecture' else None
    basis={'generation':task['generation'],'gate':gate,'artifacts':artifacts,'documents':documents,'semantic_review':semantic,'stack_decisions':stack_decisions}
    import hashlib
    raw=json.dumps(basis,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    if len(raw.encode())>6_000_000:raise HTTPException(413,'gate review exceeds snapshot limit')
    revision='gate:'+hashlib.sha256(raw.encode()).hexdigest()
    review={'revision':revision,'phase_key':gate['after_phase'],'task':task,'documents':documents,'semantic_review':semantic,'stack_decisions':stack_decisions}
    existing=store.decision_review(task_id,'gate:'+gate_key,revision)
    if existing is not None:
        # Task heartbeat/cost metadata is not review identity. Preserve the first
        # snapshot, while rejecting any conflict in the actual hashed evidence.
        old_task=existing.get('task',{})
        old_gate=next((g for g in old_task.get('gates',[]) if g.get('gate_key')==gate_key),None)
        old_artifacts=[a for a in old_task.get('artifacts',[]) if a['phase_key']==gate['after_phase'] and not a.get('stale')]
        if (old_task.get('generation')!=task['generation'] or old_gate!=gate or old_artifacts!=artifacts
            or any(existing.get(key)!=review.get(key) for key in ('phase_key','documents','semantic_review','stack_decisions'))):
            raise HTTPException(409,'retained gate evidence conflicts with its revision')
        return existing
    try:store.retain_decision_review(task_id,'gate:'+gate_key,revision,review)
    except ValueError as exc:raise HTTPException(409,'retained gate evidence conflicts with its revision') from exc
    return review

def _closed_revision_draft_available(task_id,gate_key,review):
    from .gate_revision import closed_revision_preview
    try:closed_revision_preview(store,task_id,gate_key,review['revision'],'preview eligibility')
    except (ValueError,KeyError):return False
    return True

@app.get('/api/tasks/{task_id}/gates/{gate_key}/review')
def get_gate_review(task_id:str,gate_key:str,revision:str|None=None,draft:str|None=None,a:auth.Actor=Depends(auth.require_actor)):
    current=_gate_review(task_id,gate_key)
    review=store.decision_review(task_id,'gate:'+gate_key,revision) if revision else current
    if review is None:raise HTTPException(404,'gate review revision not found')
    gate=next(g for g in current['task']['gates'] if g['gate_key']==gate_key)
    saved=store.decision_draft(task_id,'gate:'+gate_key,review['revision'],draft) if draft else None
    if draft and saved is None:raise HTTPException(404,'gate draft not found')
    return {'review':review,'actionable':review['revision']==current['revision'] and gate['state']=='waiting',
            'draft':saved,'comments':store.review_comments(task_id,'gate:'+gate_key,review['revision']),'closed_revision_draft':_closed_revision_draft_available(task_id,gate_key,review)}

@app.post('/api/tasks/{task_id}/gates/{gate_key}/comments')
def comment_gate(task_id:str,gate_key:str,body:DecisionComment,a:auth.Actor=Depends(auth.require_actor)):
    return comment_decision_review(task_id,'gate:'+gate_key,body,a)

@app.post('/api/tasks/{task_id}/gates/{gate_key}/drafts')
def draft_gate(task_id:str,gate_key:str,body:DecisionDraft,a:auth.Actor=Depends(auth.require_actor)):
    if store.decision_review(task_id,'gate:'+gate_key,body.revision) is None:raise HTTPException(404,'gate revision not found')
    return {'draft_id':store.save_decision_draft(task_id,'gate:'+gate_key,body.revision,body.model_dump())}

class ClosedGateRevisionDraft(BaseModel):
    review_revision:str=Field(min_length=1,max_length=200)
    edited_text:str=Field(min_length=1,max_length=20000)

@app.post('/api/tasks/{task_id}/gates/{gate_key}/revision-drafts')
def draft_closed_gate_revision(task_id:str,gate_key:str,body:ClosedGateRevisionDraft,a:auth.Actor=Depends(auth.require_actor)):
    # Draft only: no artifact/checkpoint rewrite, invalidation or dispatch.
    from .gate_revision import save_closed_revision
    try:return save_closed_revision(store,task_id,gate_key,body.review_revision,body.edited_text)
    except KeyError:raise HTTPException(404,'task or gate not found')
    except ValueError as exc:raise HTTPException(409,str(exc))

class SemanticDecisions(BaseModel):
    revision:str=Field(pattern=r'^[a-f0-9]{64}$')
    decisions:list[dict]=Field(max_length=1000)

@app.post('/api/tasks/{task_id}/gates/{gate_key}/semantic-review')
@_guard_task_file_operation
def decide_semantic_gate(task_id:str,gate_key:str,body:SemanticDecisions,a:auth.Actor=Depends(auth.require_actor)):
    from vmodel_contracts.semantic_review import SemanticReview
    from vmodel_contracts import SRSDocument
    live=_gate_review(task_id,gate_key);semantic=live.get('semantic_review')
    gate=next(g for g in live['task']['gates'] if g['gate_key']==gate_key)
    if not semantic or semantic['revision']!=body.revision or gate['state']!='waiting':raise HTTPException(409,'semantic review changed')
    try:
        review=SemanticReview.model_validate({**semantic,'decisions':[{**d,'actor':a.label} for d in body.decisions]})
        # Candidate is the producer body. The rendered SRS also includes
        # deterministic intent-carried fields, which must survive review.
        current=task_artifact_view(task_id,'srs','srs.json')
        full=json.loads(current['raw_content'])
        prior=SemanticReview.model_validate(semantic)
        basis=prior.resolved_candidate(lambda c:None) if prior.decisions else prior.candidate
        if any(full.get(k)!=v for k,v in basis.items()) and any(full.get(k)!=v for k,v in prior.candidate.items()):raise HTTPException(409,'SRS candidate changed outside its review')
        resolved=review.resolved_candidate(lambda candidate:SRSDocument.model_validate({**full,**candidate}))
    except ValueError as exc:raise HTTPException(422,str(exc))
    _lineage_decision_preflight(task_id)
    path=_srs_semantic_path(task_id);artifact=_task_artifact(task_id,'srs','srs.json')
    # The revision record is consumed into the producer checkpoint on resume.
    # Never create an artifact without the companion override record.
    temporary=path.with_suffix('.tmp');temporary.write_text(review.model_dump_json(indent=2));temporary.replace(path)
    full={**full,**resolved};temporary=artifact.with_suffix('.tmp');temporary.write_text(json.dumps(full,indent=2));temporary.replace(artifact)
    # A stale readable companion must not mask the reviewed canonical JSON.
    artifact.with_suffix('.md').unlink(missing_ok=True)
    # Founder item decisions are durable review evidence; republish so the
    # manifest matches local bytes and a later restore does not refuse the batch.
    from .artifact_publication import publish_committed_session
    publish_committed_session(store,task_id,path.parent.parent,phase_key='srs')
    _lineage_rebind_decision(task_id,'srs',('srs.json','srs.semantic-review.json'))
    store.add_event(task_id,'semantic_review','Semantic candidate decisions recorded',phase_key='srs',payload={'revision':review.revision,'decisions':[d.model_dump() for d in review.decisions],'actor':a.label})
    return {'status':'reviewed','revision':review.revision}

class StackDecision(BaseModel):
    key:str=Field(min_length=1,max_length=200)
    text:str=Field(max_length=2000)
    reason:str=Field(default="",max_length=1000)

class StackDecisions(BaseModel):
    revision:str=Field(pattern=r'^[a-f0-9]{64}$')
    decisions:list[StackDecision]=Field(min_length=1,max_length=200)

@app.post('/api/tasks/{task_id}/gates/{gate_key}/stack-decisions')
@_guard_task_file_operation
def decide_stack_gate(task_id:str,gate_key:str,body:StackDecisions,a:auth.Actor=Depends(auth.require_actor)):
    """Save a founder override. Reason is optional; system rationale stays separate."""
    live=_gate_review(task_id,gate_key);data=live.get('stack_decisions')
    gate=next(g for g in live['task']['gates'] if g['gate_key']==gate_key)
    if not data or data['revision']!=body.revision or gate['state']!='waiting':raise HTTPException(409,'architecture decisions changed')
    keys={s['key'] for s in data['slots']}
    for d in body.decisions:
        if d.key not in keys:raise HTTPException(422,f'unknown slot {d.key}')
        if not d.text.strip():raise HTTPException(422,'a choice is required')
    from datetime import datetime,timezone
    at=datetime.now(timezone.utc).isoformat()
    made={d.key:{'text':d.text.strip(),'reason':d.reason.strip(),'actor':a.label,'at':at} for d in body.decisions}
    _lineage_decision_preflight(task_id)
    artifact=_task_artifact(task_id,'architecture','architecture.json')
    full=json.loads(artifact.read_text())
    for entry in full.get('stack_entry_dispositions') or []:
        if entry.get('key') in made:entry['founder_decision']=made[entry['key']]
    companion={**data,'slots':[{**s,'founder_decision':made.get(s['key'],s.get('founder_decision')),
                               'prefill':None if s['key'] in made else s.get('prefill')} for s in data['slots']]}
    # Companion first: spec-generator consumes it on resume; the artifact shows the same decisions.
    path=_stack_decisions_path(task_id);temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(companion,indent=2));temporary.replace(path)
    temporary=artifact.with_suffix('.tmp');temporary.write_text(json.dumps(full,indent=2));temporary.replace(artifact)
    artifact.with_suffix('.md').unlink(missing_ok=True)
    # The companion is the durable founder-decision record and must cross hosts:
    # republish so the manifest matches the local bytes (otherwise a later
    # completed-phase restore on this or another host refuses the batch).
    from .artifact_publication import publish_committed_session
    publish_committed_session(store,task_id,path.parent.parent,phase_key='architecture')
    _lineage_rebind_decision(task_id,'architecture',('architecture.json','architecture.decisions.json'))
    store.add_event(task_id,'stack_decisions','Architecture stack decisions recorded',phase_key='architecture',payload={'decided':sorted(made),'open':_open_stack_slots(companion),'actor':a.label})
    return {'status':'saved','revision':data['revision'],'open':_open_stack_slots(companion)}

class GateDecision(BaseModel):
    action: Literal["approve","edit","redo"]
    selected_option: str|None=None
    edited_text: str|None=None
    steer: str|None=None
    expected_revision:int|None=Field(default=None,ge=0)
    review_revision:str|None=Field(default=None,max_length=240)
    comment:str=Field(default="",max_length=7000)
    model_route:Literal["ollama","deepseek"]|None=None
    model:str|None=None
@app.post("/api/tasks/{task_id}/gates/{gate_key}/decide")
async def decide_gate(task_id:str,gate_key:str,req:GateDecision,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    if config.CLOUD_TENANTS:
        if not store.task(task_id):raise HTTPException(404,'task not found')
        if not cloud_execution_admitted():raise HTTPException(503,'Cloud admission and lifecycle validation pending')

    try:
        if req.review_revision and _gate_review(task_id,gate_key)['revision']!=req.review_revision:
            raise HTTPException(409,'gate evidence changed; review the current artifact before deciding')
        if req.model_route=="deepseek" and not os.environ.get("DEEPSEEK_API_KEY"):raise ValueError("DeepSeek is not configured")
        before=store.task(task_id)
        pending=next((g for g in before["gates"] if g["gate_key"]==gate_key),None) if before else None
        if not pending:raise KeyError(gate_key)
        if pending["state"]=="waiting" and req.action in ("approve","edit"):
            # Runs first: on a restore-enabled host this materializes the committed
            # evidence (companions included) before the enforcement checks below read it.
            evidence=[a for a in _task_artifact_snapshot(task_id) if a["phase_key"]==pending["after_phase"] and not a.get("stale") and a.get("present",True)]
            if not evidence or not any(Path(a["ref"]).is_file() and a.get("valid") is not False for a in evidence):
                raise ValueError("No current valid artifact is available to review at this gate")
        if pending['after_phase']=='srs' and req.action!='redo' and _recorded_engine_session(task_id):
            # Every srs run produces its semantic review (spec-generator graph node_srs),
            # so the file is required approval evidence for a session-recorded task.
            # Fail closed when it is absent - never skip the reviewed-write check silently.
            semantic_path=_srs_semantic_path(task_id)
            if not semantic_path.is_file():
                raise ValueError('Required approval evidence is missing: the semantic review of the SRS produced in this session (srs.semantic-review.json) is not on this host. Restore the published artifacts on this host, or publish them on the producing host (scripts/ops-backfill-artifact-manifest.py), then review again - this gate does not approve without the reviewed-write evidence.')
            from vmodel_contracts.semantic_review import SemanticReview
            semantic=SemanticReview.model_validate_json(semantic_path.read_text())
            resolved=semantic.resolved_candidate(lambda candidate:None)
            canonical=json.loads(task_artifact_view(task_id,'srs','srs.json')['raw_content'])
            if any(canonical.get(k)!=v for k,v in resolved.items()):raise ValueError('Reviewed SRS write is incomplete; save the item decisions again before approving')
        if pending['after_phase']=='architecture' and req.action!='redo':
            # research_required slots in the canonical artifact mean the generator
            # wrote the founder-decision companion; it is required approval evidence.
            try:
                full=json.loads(_task_artifact(task_id,'architecture','architecture.json').read_text())
                research_required=[d for d in full.get('stack_entry_dispositions') or [] if str(d.get('status'))=='research_required']
            except HTTPException:
                research_required=[]
            decisions=_read_stack_decisions(task_id)
            if research_required and decisions is None:
                raise ValueError('Required approval evidence is missing: this architecture has research-required stack slots but architecture.decisions.json is not on this host. Restore the published artifacts on this host, or publish them on the producing host (scripts/ops-backfill-artifact-manifest.py), then review again - this gate does not approve while the founder-decision record is unavailable.')
            still_open=_open_stack_slots(decisions)
            if still_open:raise ValueError(f'{len(still_open)} architecture decision(s) still open; decide each one before approving')
        from .phases import PHASE_BY_KEY, successor
        target=pending["after_phase"] if req.action=="redo" else (successor(pending["after_phase"]).key if successor(pending["after_phase"]) else pending["after_phase"])
        if req.model_route and PHASE_BY_KEY[target].executor!="generator":
            raise ValueError("This gate has no generator phase for a model override")
        if config.CLOUD_TENANTS and req.model_route and req.model_route not in local_route.managed_routes():
            raise ValueError("That model route is not available on this server.")
        _lineage_decision_preflight(task_id)
        selected=model_route_lib.route_value(req.model_route,req.model) if req.model_route else None
        if PHASE_BY_KEY[target].executor=="generator" and not selected:
            model_route_lib.task_default_model(store,task_id,PHASE_BY_KEY[target].generator_stage)
        if pending['after_phase']=='architecture' and pending['state']=='waiting' and req.action=='approve':
            _use_architecture_defaults(task_id)
        if config.CLOUD_TENANTS:
            gate,job_id,target_phase=store.prepare_gate_dispatch(task_id,gate_key,req.action,actor=a.label,
                model=selected,comment=req.comment,review_revision=req.review_revision,
                selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,
                expected_revision=req.expected_revision)
            if req.action=='approve':_lineage_gate_settled(task_id,pending['after_phase'])
            elif req.action=='redo':_lineage_gate_redo_invalidate(task_id,pending['after_phase'])
            if job_id:
                from .tenant_jobs import dispatch_phase
                await dispatch_phase(store,orchestrator,task_id,target_phase,queued_job_id=job_id)
            return gate
        gate=store.decide_gate(task_id,gate_key,req.action,selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,actor=a.label,expected_revision=req.expected_revision)
        phase=gate["after_phase"]
        if req.action=='approve':_lineage_gate_settled(task_id,phase)
        elif req.action=='redo':_lineage_gate_redo_invalidate(task_id,phase)
        if req.comment.strip():
            store.add_review_comment(task_id,'gate:'+gate_key,req.review_revision or str(req.expected_revision),a.label,req.comment)
        if selected:
            store.set_model_default(task_id,selected)
            store.add_event(task_id,"model_route",f"{target} override uses {selected}",phase_key=target,payload={"route":req.model_route,"resolved":selected,"scope":"phase_override","actor":a.label})
        if req.action=="redo":spawn(orchestrator.execute_phase(task_id,phase,mode=ExecutionMode.REDO,model_route=selected))
        else:spawn(orchestrator.continue_after(task_id,phase,model_route=selected))
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
    root=authorized_session(artifacts_root(os.environ.get("VMODEL_ARTIFACTS_DIR")),session,store)/"interview"/"categorization-shadow.jsonl"
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

def _engine_log_owner(session_id:str):
    # Owner attribution for engine lines. The local store has no engine_sessions
    # read (sole-operator mode shows everything to the operator anyway); a store
    # without it resolves to None, which keeps the line operator-only.
    binding_reader=getattr(store,'session_binding',None)
    if binding_reader is None:return None
    binding=binding_reader(session_id)
    return binding.get('owner_id') if binding else None


@app.get("/api/server-log")
def server_log(minutes:int=Query(30,ge=1,le=240),a:auth.Actor=Depends(auth.require_actor),task_id:str|None=Query(None,max_length=240)):
    # Scrubbed, bounded, in-memory. A customer gets only lines made while serving their own requests; the operator also gets system lines.
    result=server_log_buffer.read(a.id,bool(a.admin),minutes)
    engine=engine_log.read(a.id,bool(a.admin),minutes,_engine_log_owner)
    if engine['available'] and engine['text']:
        result['text']=result['text']+('\n' if result['text'] else '')+engine['text']
    result['engine_log']={'available':engine['available'],'availability':engine.get('availability','available' if engine['available'] else 'missing'),'category':engine.get('category'),'lines':engine['count'],'path':engine['path']}
    if isinstance(task_id,str) and task_id:
        from .debug_export import _owned_log_line
        task=store.task(task_id)
        if not task:raise HTTPException(404,'task not found')
        session=task.get('engine_session_id') or ''
        result['text']='\n'.join(line for line in result['text'].splitlines() if _owned_log_line(line,task_id,session))
        result['count']=len(result['text'].splitlines())
        result['task_id']=task_id
        result['task_filter']='exact attributed task/session fields only; unattributed system/continuation lines omitted'

    return result

@app.get("/health")
def health():
    if control_plane_only():return {"ok":True,"runtime_profile":"control_plane_only","engine_execution":False,"payments_enabled":False}
    if config.CLOUD_TENANTS:return {"ok":True,"runtime_id":RUNTIME_VERSION["runtime_id"],"repositories":RUNTIME_VERSION["repositories"],"built_at":RUNTIME_VERSION.get("built_at")}
    return {"ok": True, "total_spend_usd": store.total_spend(), "tasks": len(store.list_tasks()), **RUNTIME_VERSION}

@app.post('/api/tasks/{task_id}/packages')
def create_final_package(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    from .final_package import producer_handoff,package,PackageBlocked
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    if config.CLOUD_TENANTS:raise HTTPException(503,'Cloud package export pending customer-safe diagnostics contract')
    try:
        session,root,interview,spec=orchestrator._paths(task_id)
        if task['state']!='done':raise PackageBlocked('Final task approval is required')
        for phase in task['phases']:
            if phase['state'] not in ('approved','done'):raise PackageBlocked('Phase not approved: '+phase['phase_key'])
            from .phases import PHASE_BY_KEY
            canonical=[(interview if phase['phase_key'] in ('intent','conops') else spec)/name for name in PHASE_BY_KEY[phase['phase_key']].artifacts]
            present=[path for path in canonical if path.is_file()]
            if not present or phase['phase_key'] not in ('intent','conops') and any(path.stat().st_mtime<float(phase.get('started_at') or 0) for path in present):raise PackageBlocked('Missing or stale output: '+phase['phase_key'])
        handoff=producer_handoff(interview,spec,spec/'system-baseline.json')
        result=package(task,session,handoff,root.parent/'packages'/task_id/str(task['generation']))
        url=f"/api/tasks/{task_id}/packages/{result['package_id']}?generation={task['generation']}"
        store.add_event(task_id,'final_package','Coding-agent package created',payload={'package_id':result['package_id'],'generation':task['generation'],'url':url})
        return {k:v for k,v in {**result,'url':url,'status':'ready'}.items() if k!='path'}
    except (ValueError,OSError,__import__('subprocess').TimeoutExpired) as exc:raise HTTPException(409,{'error':'Final package is blocked','cause':str(exc),'next':'Review the affected phase or open founder inputs before exporting.'})

@app.get('/api/tasks/{task_id}/packages/{package_id}')
def download_final_package(task_id:str,package_id:str,generation:int,a:auth.Actor=Depends(auth.require_actor)):
    if not __import__('re').fullmatch(r'[a-f0-9]{64}',package_id) or generation<1:raise HTTPException(404,'package not found')
    task=store.task(task_id)
    if not task:
        activity("task.export.refused","Package download refused: no such task",task_id=task_id,export="package",actor=a.label,reason="not_found")
        raise HTTPException(404,'task not found')
    if config.CLOUD_TENANTS:raise HTTPException(503,'Cloud package export pending customer-safe diagnostics contract')
    from vmodel_contracts.runtime import artifacts_root
    package_root=authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),store.engine_session(task_id),store).parent if config.CLOUD_TENANTS else artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR'))
    path=package_root/'packages'/task_id/str(generation)/(package_id+'.zip')
    if not path.is_file() or path.is_symlink():
        activity("task.export.refused","Package download refused: not found",task_id=task_id,export="package",actor=a.label,reason="not_found")
        raise HTTPException(404,'package not found')
    activity("task.export","Package downloaded",task_id=task_id,export="package",actor=a.label,generation=generation)
    return FileResponse(path,media_type='application/zip',filename=f"coding-agent-{task_id}-g{generation}.zip")


@app.get('/api/worker-jobs/recovery')
def recoverable_worker_jobs(a:auth.Actor=Depends(auth.require_actor)):
    if not config.CLOUD_TENANTS:raise HTTPException(404,'cloud worker recovery unavailable')
    return {'jobs':store.recoverable_worker_jobs(),'execution_enabled':cloud_execution_admitted(),
            'reason':'Admission and full cloud lifecycle tests pending'}

class WorkerResumeRequest(BaseModel):
    job_id:str=Field(min_length=1,max_length=128)
    phase:str=Field(min_length=1,max_length=64)

@app.post('/api/tasks/{task_id}/worker-resume')
async def resume_cloud_worker(task_id:str,body:WorkerResumeRequest,a:auth.Actor=Depends(auth.require_actor)):
    if not config.CLOUD_TENANTS:raise HTTPException(404,'cloud worker recovery unavailable')
    task=store.task(task_id)
    job=store.worker_job(body.job_id)
    if not task or not job or job['task_id']!=task_id:raise HTTPException(404,'worker job not found')
    if job['kind']!='phase:'+body.phase:raise HTTPException(409,'worker job phase mismatch')
    # Full admission chain must run before pause/reauthorize/dispatch mutations.
    # Gate is closed by default; nothing mutates the durable job until it opens.
    if not cloud_execution_admitted():raise HTTPException(503,'Cloud admission and lifecycle validation pending')
    from .tenant_jobs import dispatch_phase
    try:await dispatch_phase(store,orchestrator,task_id,body.phase,resume_job_id=body.job_id)
    except (PermissionError,ValueError) as exc:raise HTTPException(409,str(exc))
    return {'status':'resumed','job_id':body.job_id,'job':store.worker_job(body.job_id)}


def cloud_execution_admitted()->bool:
    """Closed unless the operator opened it (DELIVERY_CLOUD_EXECUTION=1) and every startup check passes; see cloud_execution."""
    if control_plane_only():return False
    from . import cloud_execution
    return cloud_execution.admitted()


@app.get('/api/admin/operations/orphaned-tasks')
def admin_orphaned_tasks(a:auth.Actor=Depends(auth.require_admin)):
    from .recovery import orphan_review_items
    return {'tasks':orphan_review_items(store),'scope':'current_authorized_workspace','auto_restart':False}


@app.get('/api/admin/operations/workspace')
def admin_operations_workspace(a:auth.Actor=Depends(auth.require_admin)):
    if config.CLOUD_TENANTS:return store.operator_workspace_snapshot()
    tasks=store.list_tasks()
    return {'scope':'current_authorized_workspace','owner_id':a.id,'credits':None,
        'sessions':[{'task_id':t['task_id'],'session_id':store.engine_session(t['task_id']),
                     'state':t['state'],'generation':t.get('generation',1)} for t in tasks if store.engine_session(t['task_id'])],
        'worker_jobs':[],'credit_adjustment_available':False,'global_operator_access':False,
        'reason':'Local legacy quota is not a prepaid credit account.'}


@app.get('/api/debug/evidence')
def debug_evidence(task_id:str|None=Query(None,max_length=240),a:auth.Actor=Depends(auth.require_actor)):
    if not a.admin:raise HTTPException(403,'operator access required')
    if config.CLOUD_TENANTS:_operator_reader()
    return diagnostic_evidence.read(task_id=task_id)


@app.get('/api/debug/log-durability')
def debug_log_durability(a:auth.Actor=Depends(auth.require_actor)):
    if not a.admin:raise HTTPException(403,'operator access required')
    from .log_durability import snapshot
    return snapshot()


@app.get('/api/debug/preflight')
def debug_preflight(a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in user's own container startup report (checked/expected/found/where). No secrets: paths, table names and build ids only.
    from . import preflight_read
    result={'startup': preflight_read.startup_report()}
    if os.environ.get('REQUI_PREFLIGHT_GENERATED_AT'):result['persistence']=preflight_read.persistence_status()
    return result


@app.get('/api/admin/operations/request-costs')
def admin_operations_request_costs(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: where request time went, per request type and for the slowest recent
    # requests, over a bounded rolling window. One process, no customer content, no raw ids.
    from . import request_costs
    return request_costs.summary()


@app.get('/api/admin/operations/capacity')
def admin_operations_capacity(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: slots, running phases and memory, to size the server. No cost, vendor or price figures. One server process: the numbers are this process's.
    from . import run_slots
    snap=run_slots.snapshot();snap['text']=run_slots.snapshot_text(snap)
    return snap


@app.get('/api/admin/operations/health')
def admin_operations_health(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: uptime, runs, CPU, memory, signed-in users. Read from this server process; no cost, vendor or customer content.
    from . import ops_health
    return ops_health.health()


@app.get('/api/admin/operations/log-download')
def admin_operations_log_download(minutes:int=Query(60,ge=1,le=240),a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: the scrubbed server log as a zip file holding one text file. Each download is recorded in the activity log with who took it.
    from . import ops_health
    from fastapi.responses import Response
    name,data,lines=ops_health.log_zip(minutes)
    activity('ops.log_download','Operator downloaded the server log',actor=a.label,minutes=minutes,lines=lines)
    return Response(content=data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="%s"'%name,'Cache-Control':'no-store'})


_stop_readers={}

@app.get('/api/admin/operations/stops')
def admin_operations_stops(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: every run stop, read through the read-only operator database role. The database registry decides, not this check alone.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    from .operator_authorization import OperatorPrincipal
    from .operator_reads import OperatorReadStore
    try:
        reader=_stop_readers.get(a.id)
        if reader is None:
            reader=_stop_readers[a.id]=OperatorReadStore(config.DATABASE_URL,OperatorPrincipal(a.id,'ops-v1',frozenset({'logs.read','sessions.read'})))
        return reader.stops(store)
    except Exception as exc:
        _stop_readers.pop(a.id,None)
        activity('ops.stops_unavailable','Operator stop list could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The stop list is not switched on yet.','cause':'The database has not been set up for operator reads.',
            'next':'Run supabase/migrations/20261020130000_operator_stop_audit.sql in the Supabase SQL editor, then reload.'})


@app.get('/api/admin/operations/attempt-releases')
def admin_operations_attempt_releases(limit:int=200,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: reservation releases and failed releases, read through the
    # read-only operator database role. The database registry decides, not this
    # check alone.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    from .operator_authorization import OperatorPrincipal
    from .operator_reads import OperatorReadStore
    try:
        reader=_stop_readers.get(a.id)
        if reader is None:
            reader=_stop_readers[a.id]=OperatorReadStore(config.DATABASE_URL,OperatorPrincipal(a.id,'ops-v1',frozenset({'logs.read','sessions.read'})))
        return reader.attempt_releases(store,limit=limit)
    except Exception as exc:
        _stop_readers.pop(a.id,None)
        activity('ops.attempt_releases_unavailable','Operator reservation-release list could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The reservation-release list is not switched on yet.','cause':'The database has not been set up for operator reads.',
            'next':'Apply the operator read role and registry used by the stop list, then reload.'})


@app.get('/api/admin/operations/spend')
def admin_operations_spend(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: credits and vendor expense per account. Users never see this; no link in the user UI.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    from .operator_authorization import OperatorPrincipal
    from .operator_reads import OperatorReadStore
    try:
        reader=_stop_readers.get(('spend',a.id))
        if reader is None:
            reader=_stop_readers[('spend',a.id)]=OperatorReadStore(config.DATABASE_URL,OperatorPrincipal(a.id,'ops-v1',frozenset({'analytics.read'})))
        return reader.spend(store)
    except Exception as exc:
        _stop_readers.pop(('spend',a.id),None)
        activity('ops.spend_unavailable','Operator credits view could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The credits page is not switched on yet.','cause':'The database has not been set up for operator reads or the credit history.',
            'next':'Run supabase/migrations/20261020130000_operator_stop_audit.sql and 20261020080000_credit_ledger.sql in the Supabase SQL editor, then reload.'})


def _grant_reader(a):
    from .operator_authorization import OperatorPrincipal
    from .operator_reads import OperatorReadStore
    reader=_stop_readers.get(('grant',a.id))
    if reader is None:
        reader=_stop_readers[('grant',a.id)]=OperatorReadStore(config.DATABASE_URL,OperatorPrincipal(a.id,'ops-v1',frozenset({'analytics.read','credits.allocate'})))
    return reader

def _grant_unavailable(a,exc):
    _stop_readers.pop(('grant',a.id),None)
    activity('ops.grant_unavailable','Operator credit grant could not be read or written',actor=a.label,error=type(exc).__name__)
    return HTTPException(503,{'error':'Granting credits is not switched on yet.','cause':'The database has not been set up for operator grants.',
        'next':'Run supabase/migrations/20261020150000_operator_grant_credits.sql in the Supabase SQL editor, then reload.'})

class GrantRequest(BaseModel):
    email: str = Field(min_length=3,max_length=320)
    credits: float = Field(gt=0,le=500)
    reason: str = Field(default='Free launch credits',min_length=1,max_length=400)
    confirm: str = ''

@app.get('/api/admin/operations/grants')
def admin_operations_grants(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: recent credit grants.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    try:return _grant_reader(a).grants(store)
    except Exception as exc:raise _grant_unavailable(a,exc)

@app.post('/api/admin/operations/grant-preview')
def admin_operations_grant_preview(body:GrantRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: what a grant would do. Writes nothing.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    try:return _grant_reader(a).grant_preview(store,body.email,body.credits)
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception as exc:
        import psycopg
        if isinstance(exc,psycopg.errors.RaiseException):raise HTTPException(400,exc.diag.message_primary or 'The grant was refused.')
        raise _grant_unavailable(a,exc)

@app.post('/api/admin/operations/grant')
def admin_operations_grant(body:GrantRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: give credits. Needs the typed word yes. The database checks the operator registry again and refuses a same-day repeat.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    if body.confirm!='yes':raise HTTPException(400,'Type yes to confirm the grant.')
    try:out=_grant_reader(a).grant_apply(store,body.email,body.credits,body.reason)
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception as exc:
        import psycopg
        if isinstance(exc,psycopg.errors.RaiseException):raise HTTPException(400,exc.diag.message_primary or 'The grant was refused.')
        raise _grant_unavailable(a,exc)
    activity('ops.credits_granted','Operator granted credits',actor=a.label,credits=body.credits,granted=bool(out.get('granted')))
    return out


@app.get('/api/admin/operations/run-metrics')
def admin_operations_run_metrics(task_id:str='',a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: CPU and memory samples. With task_id, the raw samples and summaries of that task; without, the recent-runs overview shown in Debug.
    from . import run_metrics
    return run_metrics.for_task(task_id) if task_id else run_metrics.overview()


class AdminStopRunRequest(BaseModel):
    task_id: str = Field(min_length=1,max_length=120)
    confirm: bool = False

@app.post('/api/admin/operations/stop-run')
def admin_stop_run(body:AdminStopRunRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: stop any running phase on this server. The owner is told an operator stopped it, and the audit entry is written in their workspace when the run ends.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server.')
    if config.RUN_ISOLATION!='process':raise HTTPException(409,{'error':'A run cannot be stopped on this server yet.','cause':'Runs here do not have their own process.','next':'Wait for the run to finish.'})
    from . import run_slots
    if not body.confirm:
        return {'status':'preview','running':run_slots.is_running(body.task_id),'message':'Stop the running phase of task %s? Its owner is told an operator stopped it, and it is recorded in the audit log.'%body.task_id}
    if not run_slots.request_stop(body.task_id,'operator',a.label):
        raise HTTPException(409,{'error':'Nothing is running for that task.','cause':'The phase has already finished or has not started.','next':'Reload the capacity view.'})
    activity('run.stop_requested','Operator asked to stop a run',task_id=body.task_id,actor=a.label)
    return {'status':'stopping','message':'Stopping. This takes a few seconds.'}


def _operator_reader():
    reader=getattr(app.state,'operator_read_store',None)
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Operator resource not found')
    if reader is None:
        raise HTTPException(503,'Verified sole-operator read registry is not configured')
    return reader


@app.get('/api/operator/sessions')
def operator_sessions(limit:int=100,before_created_at:float|None=None,before_session_id:str|None=None,
                      a:auth.Actor=Depends(auth.require_actor)):
    if (before_created_at is None)!=(before_session_id is None):raise HTTPException(422,'Complete session page cursor required')
    before={'created_at':before_created_at,'session_id':before_session_id} if before_session_id is not None else None
    try:return _operator_reader().sessions(store,limit=limit,before=before)
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/account/ledger')
def account_ledger_view(a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer's own credits and provider calls, read-only, for the Debug dialog. Local mode has no prepaid accounts.
    if not config.CLOUD_TENANTS:return {'text':'Not available on this server: it has no prepaid credit accounts.'}
    from .pricing import retail_rates
    return store.own_ledger(retail_rates().usd_per_credit)


class ComputeReleaseRequest(BaseModel):
    confirm: bool = False

@app.post('/api/account/compute-release')
def account_compute_release(body:ComputeReleaseRequest,a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer releases their own stuck compute records. Only records whose job never reached a provider; each is audited.
    if not config.CLOUD_TENANTS:raise HTTPException(404,'Not available on this server: it has no prepaid credit accounts.')
    from .attempt_ledger import AdmissionDenied
    try:out=store.release_own_compute(body.confirm)
    except AdmissionDenied as exc:raise HTTPException(409,{"error":"Compute records were not released.","cause":_customer_denial(exc),"next":"Open Debug, copy the ledger section and send it."})
    n=len(out['releasable']);k=len(out['kept'])
    if not body.confirm:
        out['status']='preview'
        out['message']=('Release %d stuck compute record%s? Each belongs to a job that made no provider call, so nothing is charged. It is recorded in the audit log.'%(n,'' if n==1 else 's'))+(' %d other record%s cannot be released here.'%(k,'' if k==1 else 's') if k else '')
    else:
        out['status']='released'
        out['message']='Released %d compute record%s. No charge was made.'%(out['released'],'' if out['released']==1 else 's')+(' %d could not be released.'%k if k else '')
    return out


@app.get('/api/account/credits')
def account_credits(a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer's own prepaid balance, for the header. Local mode has no prepaid accounts.
    if not config.CLOUD_TENANTS:return {'credits':None,'usd':None}
    from .pricing import retail_rates
    out=dict(store.own_credit_balance(retail_rates().usd_per_credit))
    try:out['last_topup']=store.own_last_topup()
    except Exception:
        log.warning('last top-up unavailable',exc_info=True);out['last_topup']=None   # the balance itself must still show
    return out


@app.get('/api/account/credit-history')
def account_credit_history(a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer's own credit changes, read-only, for the Debug dialog. Local mode has no prepaid accounts.
    if not config.CLOUD_TENANTS:return {'available':False,'entries':[],'text':'Not available on this server: it has no prepaid credit accounts.'}
    return store.own_credit_history()


@app.get('/api/operator/credits')
def operator_credits(limit:int=100,after_owner:str|None=None,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().credits(store,limit=limit,after_owner=after_owner)
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/operator/expense-totals')
def operator_expense_totals(a:auth.Actor=Depends(auth.require_actor)):
    return _operator_reader().expense_totals(store)


@app.get('/api/operator/vendor-status')
def operator_vendor_status(a:auth.Actor=Depends(auth.require_actor)):
    """Warnings about the vendor's published terms (a failed refresh, an old pin); calls keep running on the pinned terms."""
    _operator_reader()
    from . import vendor_status
    return vendor_status.status()


@app.get('/api/operator/task-debug')
def operator_task_debug(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().task_debug(store,task_id)
    except ValueError as exc:raise HTTPException(422,str(exc))
    except KeyError:raise HTTPException(404,'Task metadata unavailable')


@app.get('/api/operator/task-failure-diagnostics')
def operator_task_failure_diagnostics(task_id:str,limit:int=20,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().failure_diagnostics(store,task_id,limit=limit)
    except ValueError as exc:raise HTTPException(422,str(exc))
    except (HTTPException,TenantAccessDenied):raise
    except Exception as exc:
        activity('ops.failure_diagnostics_unavailable','Operator failure diagnostics could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The start-failure diagnostics are not switched on yet.','cause':'The database has not been set up for operator failure diagnostics.',
            'next':'Run supabase/migrations/20261025120000_operator_failure_diagnostics.sql in the Supabase SQL editor, then reload.'})


@app.get('/api/operator/task-logs')
def operator_task_logs(task_id:str,before_id:int|None=None,limit:int=100,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().task_logs(store,task_id,before_id=before_id,limit=limit)
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/operator/credit-account')
def operator_credit_account(owner_id:str,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().credit_account(store,owner_id)
    except ValueError as exc:raise HTTPException(422,str(exc))
    except KeyError:raise HTTPException(404,'Credit account unavailable')


@app.get('/api/operator/task-usage')
def operator_task_usage(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().task_usage(store,task_id)
    except ValueError as exc:raise HTTPException(422,str(exc))
    except KeyError:raise HTTPException(404,'Task usage unavailable')


@app.get('/api/operator/payment-journal')
def operator_payment_journal(start_at:float,end_at:float,owner_id:str|None=None,limit:int=100,
                             before_created_at:float|None=None,before_purchase_id:str|None=None,
                             a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().payment_journal(store,start_at=start_at,end_at=end_at,owner_id=owner_id,limit=limit,before_created_at=before_created_at,before_purchase_id=before_purchase_id)
    except ValueError as exc:raise HTTPException(422,str(exc))


@app.get('/api/operator/payment-totals')
def operator_payment_totals(start_at:float,end_at:float,owner_id:str|None=None,limit:int=100,
                            a:auth.Actor=Depends(auth.require_actor)):
    try:return _operator_reader().payment_totals(store,start_at=start_at,end_at=end_at,owner_id=owner_id,limit=limit)
    except ValueError as exc:raise HTTPException(422,str(exc))

