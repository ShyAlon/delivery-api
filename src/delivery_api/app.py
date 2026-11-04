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
from pydantic import BaseModel, Field, StrictBool
from fastapi.responses import JSONResponse

from vmodel_contracts import RunState, RunStatus

from . import config,local_route
from .budget import BudgetExceeded, check_global_budget, check_run_budget
from . import auth
from .jobs import JobQueue
from .runner import ProcessRunner
from .orchestrator import PhaseOrchestrator,ExecutionMode
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
store = create_store(config.DATABASE_URL)
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
        cursor=await run_in_threadpool(store.progress_cursor,task_id,"interview",session_id)
        fetched_at=time.monotonic()
        try:page=await run_in_threadpool(engine_client.progress,session_id,cursor)
        except KeyError:
            if allow_absent:return
            raise
        except EngineClientError as exc:
            if allow_absent and exc.http_status==404:return
            raise
        fetch_ms=int((time.monotonic()-fetched_at)*1000)
        ingest_at=time.monotonic()
        # Empty source page has no durable signal/cursor change. The cursor read
        # and source progress read still perform their normal tenant checks.
        if page.events:
            await run_in_threadpool(ingest_interview_page,store,task_id,session_id,page)
        elif page.next_cursor!=cursor:
            raise RuntimeError('empty progress page advanced cursor')
        if page.events:
            from datetime import datetime,timezone
            now=datetime.now(timezone.utc)
            lags=[max(0,int((now-event.timestamp).total_seconds()*1000)) for event in page.events]
            activity('progress.ingest.timing','Engine progress page retained',task_id=task_id,
                     session_id=session_id,events=len(page.events),cursor=cursor,next_cursor=page.next_cursor,
                     fetch_ms=fetch_ms,ingest_ms=int((time.monotonic()-ingest_at)*1000),
                     source_age_max_ms=max(lags),source_age_min_ms=min(lags))
        if not page.events:
            activity('progress.poll.empty','No committed progress; ingestion transaction skipped',task_id=task_id,session_id=session_id,fetch_ms=fetch_ms)
            return
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
    try:
        intent=IntentModel.model_validate_json(store.read_artifact_bytes(task_id,'intent','intent_model.json'))
        conops=ConOpsDocument.model_validate_json(store.read_artifact_bytes(task_id,'conops','conops.json'))
        if intent.session_id!=state.session_id or conops.session_id!=state.session_id:
            raise ValueError("interview artifacts belong to a different session")
    except (OSError,ValueError,KeyError) as exc:
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
    from .operator_startup import install as install_operator_reads
    install_operator_reads(application,os.environ,dsn=config.DATABASE_URL,enabled=True)
    print('Operator reads: '+application.state.operator_read_status,file=sys.stderr)
    from .process_logs import selected as selected_process_logs
    selected_process_logs()  # Ephemeral process logs; no PG writer.
    from .run_metrics import selected_metrics
    selected_metrics(config.DATABASE_URL,os.environ)
    from . import cloud_execution
    from . import cloud_runtime
    try:cloud_runtime.install(store,os.environ,_set_engine_client,watch_vendor=True)
    except Exception as exc:print('Execution runtime unavailable: '+type(exc).__name__,file=sys.stderr)
    cloud_execution.log_status()
    print("Build: "+(", ".join(f"{k} {v}" for k,v in RUNTIME_VERSION["repositories"].items()) or RUNTIME_VERSION["runtime_id"]),file=sys.stderr)
    from . import run_metrics
    run_metrics.start_idle_sampler()     # CPU and memory are read on a timer, so the operator page has numbers when no run is active
    loop_lag_task=spawn(_loop_lag_monitor())
    from .observability import LOG_PATH,stage_log_paths
    print("Server log: bounded ephemeral server files (Debug dialog)",file=sys.stderr)
    for phase,path in stage_log_paths().items():print(f"Stage log [{phase}]: {path.resolve()}",file=sys.stderr)
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
        from .process_logs import flush as flush_evidence
        await asyncio.to_thread(flush_evidence,5)
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
from .supabase_auth import SupabaseIdentityVerifier
from .tenant_http import TenantMiddleware
app.add_middleware(TenantMiddleware,verifier=SupabaseIdentityVerifier(config.SUPABASE_URL),
                   admin_subjects=config.SUPABASE_ADMIN_SUBJECTS,session_check=store.auth_session_active,
                   request_database=getattr(store,'_postgres',None))
from .tenant_files import authorized_session
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
    return _customer_events(bundles.postgres_bundle(store, session_id))


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

# Only these four POSTs talk to Supabase Auth; they write nothing to the app database.
# Allowed only with a cloud (PostgreSQL) profile; non-PG auth remains unsupported.
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
    try:
        auth.csrf_guard(request)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    started=request.state.diagnostic_started
    ref=request.state.ref
    stats=request_stats.get()
    try:response=await call_next(request)
    except Exception as error:
        log.exception("request failed ref=%s method=%s path=%s",ref,request.method,request.url.path)
        activity("http.request.failed","Request failed before response",ref=ref,method=request.method,
                 path=request.url.path,error_class=type(error).__name__,
                 duration_ms=int((time.monotonic()-started)*1000),db_queries=stats.queries,
                 db_ms=int(stats.db_seconds*1000),checkout_ms=int(stats.checkout_seconds*1000),
                 auth_ms=int(stats.auth_seconds*1000),held_ms=int(stats.tx_held_seconds*1000))
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
    boot=_json.dumps({})
    text=text.replace("<!--REQUI_CONFIG-->","<script>window.__REQUI_CONFIG__="+boot+";</script>")
    return HTMLResponse(text,headers=headers or {"Cache-Control":"no-cache"})

@app.get("/", include_in_schema=False)
def index(): return _page("index.html")

@app.get("/api/auth")
def auth_status(request: Request):
    a = auth.actor(request)
    mode="supabase"
    result={"mode":mode,"authenticated":a.id not in {"anonymous"},"actor":a.email or a.label,"admin":a.admin,
            "providers":[],"workspace":"private"}
    result['debug_features']=_account_debug_available() if a.id!='anonymous' else False
    if a.id!='anonymous':
        from .supabase_sessions import ACCESS_COOKIE
        token=request.cookies.get(ACCESS_COOKIE)
        if token:
            result['expires_at']=jwt.decode(token,options={'verify_signature':False}).get('exp')
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



@app.post("/auth/signup")
def signup(request:Request,body:BasicCredentials):
    from .supabase_sessions import signup as cloud_signup
    return cloud_signup(body.username,body.password)

@app.post("/auth/signin")
def signin(request:Request,body:BasicCredentials):
    from .supabase_sessions import signin as cloud_signin
    return cloud_signin(body.username,body.password)



@app.post("/auth/refresh")
def refresh_auth(request: Request):
    from .supabase_sessions import refresh
    return refresh(request)

@app.post("/auth/logout")
def logout(request: Request):
    from .supabase_sessions import logout as cloud_logout
    return cloud_logout(request)

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
    result=[]
    for task in store.list_tasks():
        if task.get('engine_session_id'):
            result.append(bundles.postgres_bundle(store,task['engine_session_id'])['session'])
    return {'sessions':result}



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
    owner=str(run.get("owner_id") or a.id)
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
    model_config={'extra':'forbid'}
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

class TestTaskPurge(BaseModel):
    model_config={'extra':'forbid'}
    confirm:bool=False


def _test_task_action(action):
    try:return action()
    except PermissionError:raise HTTPException(403,{'error':'This action is reserved for the reviewed test owner.','next':'For owner acceptance, review req backoffice-access --test-owner. Normal customers use their normal task list.'}) from None
    except Exception:
        # Driver details can contain content. The database refuses atomically.
        raise HTTPException(409,{'error':'Test action refused; no content was removed.',
            'cause':'Creation requires the reviewed owner. Purge requires successful reaped Start and answer jobs, settled costs, and no live lease or unreviewed content.',
            'next':'Keep the test and download Debug evidence. Resolve failed work, live processes or unsettled accounting before retrying.'}) from None

@app.post('/api/operations/test-tasks',status_code=201)
def create_test_task(body:TaskCreate,a:auth.Actor=Depends(auth.require_actor)):
    return _test_task_action(lambda:store.create_task(body.title,body.brief,is_test=True))

@app.get('/api/operations/test-tasks')
def list_test_tasks(a:auth.Actor=Depends(auth.require_actor)):
    return _test_task_action(lambda:{'tasks':store.list_test_tasks()})

@app.get('/api/operations/test-tasks/{task_id}/expense')
def test_task_expense(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    return _test_task_action(lambda:store.test_task_expense(task_id))

@app.get('/api/operations/test-tasks/{task_id}/breakdown')
def test_task_breakdown(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    return _test_task_action(lambda:store.test_task_breakdown(task_id))

@app.post('/api/operations/test-tasks/{task_id}/purge')
def purge_test_task(task_id:str,body:TestTaskPurge,a:auth.Actor=Depends(auth.require_actor)):
    return _test_task_action(lambda:store.purge_test_task(task_id,confirm=body.confirm))


def _deny_test_product_export(task_id):
    task=store.task(task_id)
    if task and task.get('is_test'):
        raise HTTPException(409,{'error':'Test data is excluded from product exports.',
            'next':'Use the owner test view or Debug evidence instead.'})

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
    return {"stoppable":bool((config.RUN_ISOLATION=="process") and (run_slots.is_running(task_id)))}


class StopRunRequest(BaseModel):
    confirm: bool = False

STOP_RUN_MESSAGE="Stopping ends this phase now. Work already saved is kept and you can start it again. A model call in progress when you stop may still be counted against your credits."

@app.post("/api/tasks/{task_id}/stop-run")
def stop_run(task_id:str,body:StopRunRequest,a:auth.Actor=Depends(auth.require_actor)):
    # The owner stops their own running phase. This ends the child process; it is not the interview engine's cooperative /stop.
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

class DeleteTaskRequest(BaseModel):
    confirm: StrictBool = False

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id:str,body:DeleteTaskRequest,a:auth.Actor=Depends(auth.require_actor)):
    try:out=store.delete_task(task_id,a.label,confirm=body.confirm)
    except KeyError:
        activity("task.delete.refused","Task delete refused: no such task",task_id=task_id,actor=a.label,reason="not_found")
        raise HTTPException(404,{"error":"Task not found.","cause":"It may already have been deleted.","next":"Return to the task list."})
    except ValueError as e:
        activity("task.delete.refused","Task Delete needs confirmation",task_id=task_id,actor=a.label,reason="confirmation")
        raise HTTPException(409,{"error":str(e),"next":"Confirm Delete to request Stop and hide this task. Saved work and accounting stay retained."})
    activity("task.deleted","Task deleted",task_id=task_id,actor=a.label)
    return {"status":"deleted",**out}

class QualityConfigRequest(BaseModel):
    config:dict
def _quality_engine_down(exc):
    return HTTPException(502,{"error":"The interview engine is not reachable.","cause":str(exc),"next":"Start the engine (scripts/run-ui.sh does it), then reload the Debug dialog."})
@app.get("/api/question-quality-config")
def get_question_quality_config(a:auth.Actor=Depends(auth.require_actor)):
    """Global interview question-quality settings; the engine owns the schema and defaults."""
    _require_account_debug()
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
def _ollama_route_choice(model=None,*,managed=False):
    """Optional picker data is never a readiness gate. Start validates the route."""
    options=[model] if model else []
    status={'availability':'unavailable','reason':'installed_lookup_failed','scope':'pinned_route_only'}
    if managed:
        status={'availability':'available','scope':'reviewed_models'}
    else:
        try:
            installed=engine_client.local_models()
            options=list(dict.fromkeys([*options,*installed]))
            status={'availability':'available','scope':'installed_models'}
        except (EngineClientError,EngineUnreachableError,AttributeError):pass
    return {'id':'ollama','available':True,'managed':managed,'readiness':'checked_on_start',
        'model':'ollama:'+model if model else None,'models':options,'model_list':status}

@app.get("/api/model-routes")
def model_routes(a:auth.Actor=Depends(auth.require_actor)):
    """The engine resolves routes; legacy env keys only seed the initial choice."""
    from . import cloud_runtime
    gateway=cloud_runtime.gateway_factory()
    open_now=cloud_execution_admitted()
    admitted_models=getattr(gateway,'models',{}) if gateway else {}
    # This endpoint belongs to the customer workspace, even for an admin.
    # Raw readiness evidence belongs only to the separate operations surface.
    closed_message='Interviews are temporarily unavailable. Please try again later.'
    routes=[]
    for route in ('deepseek','ollama'):
        if route=='ollama':
            routes.append(_ollama_route_choice(admitted_models.get('ollama'),managed=True))
            continue
        errors=[];models=None;model=None
        if route=='deepseek' and not (os.environ.get('DEEPSEEK_API_KEY') or '').strip():
            errors.append('DeepSeek is not available on this server.')
        if not open_now:errors.append(closed_message)
        if gateway is None and open_now:errors.append('Interviews are temporarily unavailable. Please try again later.')
        elif gateway is not None and route not in admitted_models:errors.append('DeepSeek is not available on this server.')
        if not errors:
            try:
                preview=engine_client.preview_route(route)
                models=preview['model_map'];pairs={tuple(pair) for pair in models.values()}
                if len(pairs)!=1:raise ValueError('engine preview returned divergent stage models')
                provider,model=next(iter(pairs))
                if provider!=route:raise ValueError(f'engine preview returned {provider} for selected {route}')
                if model!=admitted_models.get(route):raise ValueError(f'Admission gateway reviewed model is {admitted_models.get(route)}; engine selected {model}.')
            except (EngineClientError,EngineUnreachableError,KeyError,ValueError,TypeError) as exc:
                errors.append('DeepSeek is temporarily unavailable. Please try again later.')
        routes.append({'id':route,'available':not errors,'managed':True,
            **({'error':' '.join(errors)} if errors else {}),
            **({'model':f'{route}:{model}','model_map':models,'models':[model]} if model else {})})
    enabled=next((r['id'] for r in routes if r['available']),None)
    return {'routes':routes,'default_route':{'route':enabled} if enabled else None,'managed':True}
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
    if (selected_route not in ('deepseek','ollama')):
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
    from . import cloud_runtime
    installed=cloud_runtime.gateway_factory()
    reviewed=getattr(installed,'models',{}).get(selected_route) if installed else None
    if reviewed!=model:
        raise HTTPException(422,{'error':'Requested model does not match the reviewed route.',
            'cause':f'Requested {provider}:{model}; reviewed {selected_route}:{reviewed or "not configured"}.',
            'next':'Reload model routes and correct legacy initial defaults before starting. Task was not started.'})
    if selected_route=='deepseek':
        from .operator_expense_policy import configured
        if not configured(store):
            activity('operator.expense_policy.missing','Provider dispatch blocked: operator expense policy missing',task_id=task_id)
            raise HTTPException(503,'This model is temporarily unavailable. Please choose another available model or try later.')
    if (hasattr(installed,'for_task')):installed.for_task(task_id)
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
        await run_in_threadpool(store.update_phase,task_id,"intent","failed",detail=str(exc)[:300])
        await run_in_threadpool(store.add_event,task_id,"error",f"Error: the interview engine is not reachable. Likely cause: nothing accepted the connection at {exc.base_url} - the engine service is not running. Next: restart scripts/run-ui.sh (it starts the engine automatically), or start the engine by hand: {ENGINE_START_HINT}. Then retry.",phase_key="intent",payload=_engine_unreachable_payload(exc))
        return
    except EngineClientError as exc:
        payload={"route":selected_route,"http_status":exc.http_status,"protocol_error":exc.error.model_dump(mode="json"),"stage":"preflight"}
        activity("model.route.preflight.rejected","Interview route rejected by engine",task_id=task_id,**payload)
        await run_in_threadpool(store.add_event,task_id,"model_preflight",f"Interview route {selected_route} rejected: {exc.error.detail}",phase_key="intent",payload=payload)
        await run_in_threadpool(store.update_phase,task_id,"intent","failed",detail=exc.error.detail[:300])
        await run_in_threadpool(store.add_event,task_id,"error",f"Interview start failed: {exc.error.detail}",phase_key="intent",payload=payload)
        # Operator-only failure evidence: the engine's sanitized 503 block is
        # recorded on the dedicated diagnostics store - never on the events or
        # audit rows every customer surface reads.
        if getattr(exc,'operator_diagnostics',None):
            try:await run_in_threadpool(store.record_failure_diagnostics,task_id,'engine.start',exc.http_status,exc.operator_diagnostics)
            except Exception as record_exc:activity("engine.start.diagnostics_unrecorded","Operator failure diagnostics could not be recorded",task_id=task_id,error=f"{type(record_exc).__name__}")
        return
    except Exception as exc:
        activity("engine.start.failed","Interview start failed unexpectedly",task_id=task_id,error=f"{type(exc).__name__}: {str(exc)[:200]}")
        await run_in_threadpool(store.update_phase,task_id,"intent","failed",detail=f"{type(exc).__name__}: {str(exc)[:200]}")
        await run_in_threadpool(store.add_event,task_id,"error",f"Interview start failed: {type(exc).__name__}: {str(exc)[:200]}",phase_key="intent")
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
    answered_input_id=body.decision_id
    answered_question=None
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
            answered_input_id=str(pending.input_id)
            answered_question=pending.prompt
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
            submit_at=time.monotonic()
            activity('engine.answer.submit_begin','Waiting for engine input advance, not an enqueue receipt',task_id=task_id,session_id=session_id)
            try:
                submitted=await run_in_threadpool(engine_client.submit,session_id,InputResponse(input_id=pending.input_id,expected_sequence=body.expected_sequence,value=typed_value,
                    rationale=rationale or None,actor_id=a.label,founder_inputs=body.founder_inputs))
            finally:
                activity('engine.answer.submit_end','Engine input request finished; outcome is recorded separately',task_id=task_id,session_id=session_id,elapsed_ms=int((time.monotonic()-submit_at)*1000))
                drain_at=time.monotonic();progress_stop.set()
                try:await progress_watch
                finally:activity('engine.answer.progress_drain','Final answer progress watch finished',task_id=task_id,session_id=session_id,elapsed_ms=int((time.monotonic()-drain_at)*1000))
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
    active_phase=task_before.get("active_phase") or "intent"
    answer_payload={"actor":a.label,"character_count":len(body.answer),"transcript":True,"decision_id":answered_input_id,"input_id":answered_input_id,"question":answered_question,"answer":body.answer}
    if answer_display:answer_payload["answer_display"]=answer_display
    store.add_event(task_id,"answer",f"You: {answer_display or body.answer}",phase_key=active_phase,payload=answer_payload)
    activity("engine.answer","Founder answer submitted",task_id=task_id,phase=active_phase,actor=a.label,character_count=len(body.answer))
    if source=="http_interview":
        _finish_cloud_interview(task_id,submitted.snapshot)
    return {"status":"accepted"}


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

def _task_artifact_snapshot(task_id:str, *, task=None, session=_SESSION_UNSET, events=None, stored_artifacts=None, manifest=_SESSION_UNSET)->list[dict]:
    """One owned PG publication is the persistent artifact authority."""
    try:snapshot=store.manifest_snapshot(task_id) if manifest is _SESSION_UNSET else manifest
    except ValueError:
        log.exception('artifact manifest integrity failure task=%s',task_id)
        raise HTTPException(500,'artifact manifest failed its integrity check')
    if snapshot is not None:return _project_manifest_rows(snapshot['rows'])
    if (store.task_artifacts(task_id) if stored_artifacts is None else stored_artifacts):
        raise HTTPException(500,'artifact manifest coverage is incomplete; no ready published revision for this generation')
    return []


_OUTPUT_PARTIALS={"srs":"srs_fills.partial.json","architecture":"architecture.partial.json",
                  "specs":"items.partial.json","tests":"tests_fills.partial.json",
                  "validation":"validation_fills.partial.json"}

@app.get("/api/tasks/{task_id}/phase-output")
def task_phase_output(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    """Current phase output only. Partial checkpoints are atomic producer writes, not control."""
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    phase=task.get("active_phase");generation=task.get("generation")
    from .pg_phase_output import phase_output
    return phase_output(store,task)

@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}")
def task_artifact_download(task_id:str,phase:str,name:str):
    _deny_test_product_export(task_id)
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

_ARTIFACT_TITLES={"srs.semantic-review.json":"Requirements Semantic Review","architecture.decisions.json":"Architecture Stack Decisions","intent_model.json":"Needs and Scope","conops.json":"Concept of Operations","needs.json":"Stakeholder Needs","srs.json":"Requirements","architecture.json":"Architecture","items.json":"Item Specifications","spec_items.json":"Item Specifications","tests.json":"Verification","test_specifications.json":"Verification","validation.json":"Validation"}

def _interview_model_provenance(task_id:str,status=None)->list[str]:
    """Usage is per-call truth; never collapse a switched stage to its first model."""
    def mark(availability,reason):
        if status is not None:status['model_usage']={'availability':availability,'reason':reason}
    mark('absent','no_saved_usage')
    mark('policy_excluded','provider_usage_excluded');return []

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
    matched_answers={}
    for event in _customer_events(store.events(task_id)):
        if event.get("phase_key") not in ("intent","conops"):continue
        payload=event.get("payload") or {}
        identity=payload.get('input_id') or payload.get('decision_id')
        if event.get("kind") in ("waiting_input","founder_decision"):
            question=str(payload.get("question") or event.get("message") or "").strip()
            if question and not any(identity and turn[2]==identity for turn in turns):
                turns.append([question,None,identity])
        elif event.get("kind")=="answer":
            message=event.get("message","")
            answer=str(payload.get("answer_display") or payload.get("answer") or (message.removeprefix("You: ") if message.startswith("You: ") else "")).strip()
            target=next((turn for turn in reversed(turns) if turn[1] is None and (not identity or turn[2]==identity)),None)
            if answer and target is not None:
                target[1]=answer
                matched_answers[event['id']]=(target[0],answer)
    supplemental={}
    models=_interview_model_provenance(task_id,supplemental)
    notes=[f"Supplemental {name}: {row['availability']} ({row['reason']}). Primary artifact and recorded events are preserved." for name,row in supplemental.items() if row['availability'] in ('unavailable','absent')]
    events=_customer_events(store.events(task_id))
    if not matched_answers and not notes and not any(e.get('kind')=='model_route' and (e.get('payload') or {}).get('scope')=='switch' for e in events):return ""
    lines=models+notes+["","## Interview questions and answers"]
    numbered=0
    for event in events:
        if event.get("phase_key") not in ("intent","conops"):continue
        payload=event.get("payload") or {}
        if event.get("kind")=="model_route" and payload.get("scope")=="switch":
            lines.extend(["",f"**{event.get('message','Model switched')}** · {payload.get('timestamp','unknown time')} · segment {payload.get('route_version','?')} · actor {payload.get('actor','unknown')}"])
        elif event.get('id') in matched_answers:
            question,answer=matched_answers[event['id']]
            numbered+=1
            lines.extend(["",f"### Question {numbered}",question,"",f"**Answer:** {answer}"])
    return "\n".join(lines)


def _decision_provenance(task_id:str,architecture:dict)->list[dict]:
    """Derived, conservative provenance. Empty baseline refs do not prove founder intent."""
    session=_recorded_engine_session(task_id)
    from vmodel_contracts.runtime import artifacts_root
    preferences=[]
    preference_status={'availability':'absent','reason':'no_saved_intent'}
    try:
        from vmodel_contracts.interview import IntentModel
        intent=IntentModel.model_validate_json(store.read_artifact_bytes(task_id,'intent','intent_model.json'))
        preference_status={'availability':'available','reason':'saved_intent'}
        preferences=[{'area':p.area.value,'preference':p.preference,'strength':p.strength.value,'source_turns':p.source_turns} for p in intent.tech_preferences]
    except KeyError:pass
    except ValueError:preference_status={'availability':'unavailable','reason':'invalid_or_unreadable'}
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
            value=json.loads(store.read_artifact_bytes(task_id,phase,name))
            availability[phase]={'availability':'available'};return value
        except (HTTPException,KeyError):
            availability[phase]={'availability':'absent','reason':'no_saved_artifact'};return None
        except (OSError,ValueError):
            availability[phase]={'availability':'unavailable','reason':'invalid_or_unreadable'};return None
    try:validation=json.loads(raw)
    except ValueError:return None
    ledger=None
    session=_recorded_engine_session(task_id)
    if session:
        from vmodel_contracts.runtime import artifacts_root
        try:
            from .generator_documents import GeneratorDocuments
            from .worker_capability import WorkerCapability
            raw_ledger=GeneratorDocuments.owner_reader(store,task_id).read('spec/findings-ledger.json')
            if raw_ledger is None:raise FileNotFoundError('no saved ledger')
            ledger=json.loads(raw_ledger)
        except FileNotFoundError:availability['findings_ledger']={'availability':'absent','reason':'no_saved_ledger'};ledger=None
        except (OSError,ValueError):availability['findings_ledger']={'availability':'unavailable','reason':'invalid_or_unreadable'};ledger=None
    panel=validation_panel(validation,load("needs","needs.json"),load("tests","tests.json"),ledger)
    if isinstance(panel,dict):panel['supplemental_availability']=availability
    return panel

@app.get("/api/tasks/{task_id}/artifacts/{phase}/{name}/view")
def task_artifact_view(task_id:str,phase:str,name:str):
    from .phases import PHASE_BY_KEY
    if phase not in PHASE_BY_KEY or name not in PHASE_BY_KEY[phase].artifacts:raise HTTPException(404,'artifact not found')
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    binding=store.session_binding(task['engine_session_id'])
    try:data=store.read_artifact_bytes(task_id,phase,name,generation=binding['generation'])
    except KeyError:raise HTTPException(404,'artifact not found')
    if len(data)>2_000_000:raise HTTPException(413,'artifact is too large for inline viewing')
    text=data.decode('utf-8',errors='replace');raw=text;display_name=name;display_format='json' if name.endswith('.json') else 'text'
    if name.endswith('.json'):
        from .worker_capability import WorkerCapability
        from .generator_documents import GeneratorDocuments
        reader=GeneratorDocuments.owner_reader(store,task_id)
        sibling=Path(name).with_suffix('.md').name
        directory='interview/' if phase in ('intent','conops') else 'spec/'
        companions=reader.capture([directory+name,directory+sibling])
        readable=companions.get(directory+sibling) if companions.get(directory+name)==data else None
        if readable is not None:
            if len(readable)>2_000_000:raise HTTPException(413,'artifact is too large for inline viewing')
            text=readable.decode('utf-8',errors='replace');display_name=sibling;display_format='markdown'
        try:raw=json.dumps(json.loads(raw),indent=2)
        except ValueError:pass
    transcript=_interview_transcript_markdown(task_id) if name=='intent_model.json' else ''
    if transcript:
        if display_format!='markdown':text=f'# {_ARTIFACT_TITLES[name]}\n\n'+text;display_format='markdown'
        text=text.rstrip()+'\n\n'+transcript+'\n'
    provenance=_decision_provenance(task_id,json.loads(raw)) if name=='architecture.json' else None
    panel=_validation_panel_for(task_id,raw) if name=='validation.json' else None
    return {'name':name,'title':_ARTIFACT_TITLES.get(name,Path(name).stem.replace('_',' ').title()),'phase':phase,'format':display_format,
        'content':text,'raw_format':'json' if name.endswith('.json') else 'text','raw_content':raw,'display_name':display_name,'decision_provenance':provenance,'validation_panel':panel}

@app.get("/api/tasks/{task_id}/artifacts.zip")
def task_artifacts_zip(task_id:str):
    _deny_test_product_export(task_id)
    records=_task_artifact_snapshot(task_id)
    if not records:
        activity("task.export.refused","Artifacts zip refused: no artifacts",task_id=task_id,export="zip",reason="no_artifacts")
        raise HTTPException(404,"no artifacts found")
    data=io.BytesIO()
    with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as archive:
        for artifact in records:
            entry=f"{artifact['phase_key']}/{artifact['name']}"
            try: archive.writestr(entry,store.read_artifact_bytes_sha(artifact['sha256']))
            except (KeyError,ValueError):
                log.exception('artifact integrity failure in zip task=%s entry=%s',task_id,entry)
                activity("task.export.failed","Artifacts zip failed an integrity check",task_id=task_id,export="zip",reason="integrity")
                raise HTTPException(500,'an artifact failed its stored integrity check; the failure was recorded for diagnostics')
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
        from .generator_documents import GeneratorDocuments
        raw=GeneratorDocuments.owner_reader(store,task_id).read('interview/llm-calls.jsonl')
        model_calls=diagnostics.model_calls(raw,cloud=not _account_debug_available())
    return diagnostics.build(detail,detail.get("events") or [],detail.get("control_snapshot"),detail.get("input_state"),
                             diagnostics.engine_summary(snapshot,error),worker_state=store.task_worker_state(task_id),model_calls=model_calls)

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
    elif (session_id):
        snapshot,input_state=_cloud_engine_read(task_id,task,session_id,events=task_events)
    else:input_state=({"waiting":False,"process_live":False,"decision":None,"requirement":None,"question":None,"input_id":None})
    work=None
    work_state=None
    artifacts=_task_artifact_snapshot(task_id,task=task,session=session_id,events=task_events,stored_artifacts=read_snapshot["artifacts"],manifest=read_snapshot["manifest"]);session=_recorded_engine_session(task_id,recorded=session_id,events=task_events)
    from .phases import PHASE_DEFINITIONS
    phase_config={phase.key:{"executor":phase.executor,"generator_stage":phase.generator_stage,
                             "prerequisite":phase.prerequisite,"artifacts":list(phase.artifacts)} for phase in PHASE_DEFINITIONS}
    from .control_snapshot import project as project_control
    from . import phase_start
    ready_next=phase_start.PHASE if (session_id) and (phase_start.eligible(task)) else None
    control=project_control(task,input_state,snapshot,work,artifacts,ready_next=ready_next)
    _retain_decision_control(task_id,control.model_dump(mode="json"))
    control.artifact_ref=None
    artifacts=[{key:value for key,value in row.items() if key!='ref'} for row in artifacts]
    return _customer_events({**task, "rerun_control":_rerun_control(), "control_snapshot":control.model_dump(mode="json"), "phase_config":phase_config, "events": _customer_events(task_events), "artifacts":artifacts,"artifact_projection":{"session":session,"root":None,"count":len(artifacts),"reason":"projected canonical session artifacts" if artifacts else ("session could not be recovered" if not session else "no configured artifacts found under session")}, "input_state":input_state,"active_work":work_state,"stop_control":{"available":bool((cloud_execution_admitted()) and (session_id)),"requested":task.get("state") in ("stop_requested","stopped")},"run_control":_run_control(task_id)})

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

def _require_account_debug():
    reader=getattr(app.state,'operator_read_store',None)
    if reader is None:raise HTTPException(404,'Debug resource not found')
    return reader.authorize_debug(store)


def _account_debug_available():
    try:_require_account_debug();return True
    except (HTTPException,TenantAccessDenied):return False


@app.post("/api/tasks/{task_id}/debug.zip")
def download_debug_zip(task_id:str,body:BrowserDiagnostics,a:auth.Actor=Depends(auth.require_actor)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    _require_account_debug()
    session=_recorded_engine_session(task_id)
    if not session:raise HTTPException(409,{"error":"No run is linked to this task.","cause":"A task/run-scoped diagnostic export needs a recorded session.","next":"Start a run or select a task with a run."})
    if not __import__('re').fullmatch(r'[A-Za-z0-9._-]+',session) or session in ('.','..'):raise HTTPException(409,'invalid recorded session')
    if len(json.dumps(body.model_dump()).encode())>1_000_000:raise HTTPException(413,'browser diagnostics exceed 1 MB')
    from vmodel_contracts.runtime import artifacts_root
    from .debug_export import diagnostic_zip
    from .observability import LOG_PATH
    root=authorized_session(artifacts_root(os.environ.get('VMODEL_ARTIFACTS_DIR')),session,store)
    # Diagnostic export must survive a broken live engine/status read.
    control={'task_id':task_id,'state':task['state'],'generation':task['generation'],
             'stop_requested_at':task.get('stop_requested_at'),'stopped_at':task.get('stopped_at'),
             'scope':'retained task state; engine quiescence is not inferred'}
    document_reader=None
    if hasattr(store,'_postgres'):
        from .generator_documents import GeneratorDocuments
        document_reader=GeneratorDocuments.owner_reader(store,task_id).read
    data=diagnostic_zip(root,task_id,session,task['generation'],body.model_dump(),control,store.events(task_id),LOG_PATH,engine_log.default_path(),document_reader=document_reader)
    from fastapi.responses import Response
    activity("task.export","Debug zip downloaded",task_id=task_id,export="debug_zip",actor=a.label)
    return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{task_id}-debug.zip"'})

@app.get("/api/tasks/{task_id}/execution")
def task_execution(task_id:str,debug:bool=False,a:auth.Actor=Depends(auth.require_actor)):
    """Per-session routing, usage and call diagnostics; raw payloads require debug mode."""
    if not store.task(task_id):raise HTTPException(404,"task not found")
    _require_account_debug()
    session=_recorded_engine_session(task_id)
    if not session:return {"session_id":None,"route":None,"usage":[],"calls":[],"raw":[]}
    from .generator_documents import GeneratorDocuments
    document_reader=GeneratorDocuments.owner_reader(store,task_id).read
    def lines(name,limit=300):
        raw=document_reader('interview/'+name)
        if raw is None:return []
        return [value for row in raw.decode(errors='replace').splitlines() if (value:=_execution_json(row)) is not None][-limit:]
    pinned=None
    state=document_reader('interview/http-session.json')
    if state is not None:
        try:pinned=json.loads(state).get('model_map')
        except ValueError:pass
    route=(store.task(task_id) or {}).get("model_route")
    usage=lines("usage.jsonl")
    calls=lines("llm-calls.jsonl")
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
        raw.extend(lines("debug-http.jsonl",100))
    return {"session_id":session,"route":route,"effective_models":pinned,"usage":usage,"usage_segments":_model_usage_segments(usage),"route_history":[(e.get("payload") or {}) for e in store.events(task_id) if e.get("kind")=="model_route" and (e.get("payload") or {}).get("scope")=="switch"],"calls":summary,"raw":raw}

def _execution_json(row:str):
    try:return json.loads(row)
    except ValueError:return None

def _customer_events(events):
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

def _pg_generator_checkpoint_exists(task_id):
    from .generator_checkpoint import GeneratorCheckpoint
    from .worker_capability import WorkerCapability
    cap=WorkerCapability.capture(store,task_id)
    reader=GeneratorCheckpoint(store,cap,None,'spec',writable=False)
    return reader.get_tuple({'configurable':{'thread_id':cap.session_id,'checkpoint_ns':''}}) is not None

class FailedNeedsRetryRequest(BaseModel):
    confirm: bool = False
    model_route: Literal["ollama","deepseek"]|None = None
    model: str|None = Field(default=None,min_length=1,max_length=160)

@app.post("/api/tasks/{task_id}/retry-needs")
async def retry_failed_needs(task_id:str,body:FailedNeedsRetryRequest,a:auth.Actor=Depends(auth.require_run_start)):
    task=store.task(task_id)
    if not task:raise HTTPException(404,"task not found")
    if (body.confirm) and (not cloud_execution_admitted()):
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
    try:
        intent=IntentModel.model_validate_json(store.read_artifact_bytes(task_id,'intent','intent_model.json'))
        conops=ConOpsDocument.model_validate_json(store.read_artifact_bytes(task_id,'conops','conops.json'))
        if intent.session_id!=session or conops.session_id!=session:raise ValueError("interview artifact session IDs do not match")
    except (OSError,ValueError,KeyError) as exc:
        reject(f"The linked interview artifacts are missing or invalid: {exc}","The approved interview evidence must be retained in PostgreSQL before retrying Needs.")
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
    # A first Needs attempt can die on import before LangGraph writes a
    # checkpoint. --from needs rejects that state. An existing checkpoint
    # should be resumed by a normal launch, preserving completed work.
    checkpoint=_pg_generator_checkpoint_exists(task_id)
    latest_failure=next((event for event in reversed(store.events(task_id)) if event["phase_key"]=="needs" and event["kind"]=="error"),None)
    if not body.confirm:
        return {"status":"preview","phase":"needs","session_id":session,"retains":["intent","conops"],"model_route":effective,"default_model":default,"default_source":default_source,"checkpoint":checkpoint,"mode":"resume" if checkpoint else "start",
                "last_failure":latest_failure["message"] if latest_failure else next(phase["detail"] for phase in task["phases"] if phase["phase_key"]=="needs"),
                "message":f"Retry Stakeholder needs from the approved interview. Choose the downstream model; this may make new model calls and incur cost. The interview is not reopened."}
    job_id=store.prepare_phase_retry(task_id,"needs",a.label,effective,checkpoint)
    if not job_id:reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
    await _run_failed_phase_retry(task_id,"needs","Stakeholder needs",effective,checkpoint,queued_job_id=job_id)
    return {"status":"started","phase":"needs","session_id":session,"job_id":job_id}

async def _dispatch_failed_retry(task_id:str,phase:str,model_route:str,checkpoint:bool,*,queued_job_id=None)->None:
    options={"mode":ExecutionMode.RESUME if checkpoint else ExecutionMode.START,"model_route":model_route}
    from .tenant_jobs import dispatch_phase
    await dispatch_phase(store,orchestrator,task_id,phase,queued_job_id=queued_job_id,**options)

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
    if (body.confirm) and (not cloud_execution_admitted()):
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
    try:
        intent=IntentModel.model_validate_json(store.read_artifact_bytes(task_id,'intent','intent_model.json'))
        conops=ConOpsDocument.model_validate_json(store.read_artifact_bytes(task_id,'conops','conops.json'))
        if intent.session_id!=session or conops.session_id!=session:raise ValueError("interview artifact session IDs do not match")
    except (OSError,ValueError,KeyError) as exc:
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
    checkpoint=_pg_generator_checkpoint_exists(task_id)
    latest_failure=next((event for event in reversed(store.events(task_id)) if event["phase_key"]==body.phase and event["kind"]=="error"),None)
    if not body.confirm:
        return {"status":"preview","phase":body.phase,"label":label,"session_id":session,"retains":list(PHASE_KEYS[:index]),"model_route":effective,"default_model":default,"default_source":default_source,"checkpoint":checkpoint,"mode":"resume" if checkpoint else "start",
                "last_failure":latest_failure["message"] if latest_failure else next(phase["detail"] for phase in task["phases"] if phase["phase_key"]==body.phase),
                "message":f"Retry {label} from the approved earlier phases. Choose the downstream model; this may make new model calls and incur cost. Nothing earlier is reopened."}
    job_id=store.prepare_phase_retry(task_id,body.phase,a.label,effective,checkpoint)
    if not job_id:reject("The task changed before the retry was confirmed.","Refresh and review the current phase again.")
    await _run_failed_phase_retry(task_id,body.phase,label,effective,checkpoint,queued_job_id=job_id)
    return {"status":"started","phase":body.phase,"session_id":session,"job_id":job_id}

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
    raise HTTPException(409,{"code":"tenant_rerun_unavailable",
        "error":"Stage rerun is unavailable for tenant tasks.",
        "cause":_rerun_control()["reason"],
        "next":"No work changed. Check the task Audit. Use a supported failed-phase Retry if shown; otherwise create a new task to start again (new model calls may cost credits)."})

def _is_credit_event(event:dict)->bool:
    # Events written before the credit rescale carry no scale marker and sit on the old unit scale.
    return ((event.get("payload") or {}).get("rates") or {}).get("scale")=="credits"

def _task_cost(task_id:str)->dict:
    """Running customer price: credits and USD only. Priced phases come from their
    price events; model tokens recorded in the usage files but not yet covered by an
    event (the phase is still running) and all interview tokens are added at the token
    rate, so the total grows as calls land. Interview calls carry no process-time part."""
    if hasattr(store,'task_child_cost'):
        child=store.task_child_cost(task_id)
        if child is not None:return child
    if hasattr(store,'task_retail_totals'):
        exact=store.task_retail_totals(task_id)
        if exact is not None:return exact
    from .pricing import token_credits,credits_to_usd
    latest={}
    for event in store.events(task_id):
        if event.get("kind")=="price":latest[event.get("phase_key")]=event
    session=_recorded_engine_session(task_id);interview_tokens=spec_tokens=0
    if session:
        from .pg_usage import usage_summary
        summary=usage_summary(store,task_id);evidence=summary['evidence']
        if not evidence['complete']:return {'price':None,'price_usd':None,'evidence':evidence}
        interview_tokens,spec_tokens=summary['tokens']
    counted=[e for e in latest.values() if _is_credit_event(e) and not (interview_tokens and e.get("phase_key") in ("intent","conops"))]
    event_price=sum(float((e.get("payload") or {}).get("price") or 0) for e in counted)
    event_tokens=sum(int((e.get("payload") or {}).get("tokens") or 0) for e in counted if e.get("phase_key") not in ("intent","conops"))
    price=event_price+token_credits(interview_tokens)+token_credits(max(0,spec_tokens-event_tokens))
    result={"price":round(price,6),"price_usd":round(credits_to_usd(price),6)}
    if session:result['evidence']=evidence
    return result

@app.get("/api/tasks/{task_id}/cost")
def task_cost(task_id:str,request:Request):
    if hasattr(store,'task_child_cost'):
        try:child=store.task_child_cost(task_id)
        except KeyError:raise HTTPException(404,"task not found")
        if child is not None:return child
    elif not store.task(task_id):raise HTTPException(404,"task not found")
    if hasattr(store,'task_retail_totals'):
        exact=store.task_retail_totals(task_id)
        if exact is not None:return exact
    session=_recorded_engine_session(task_id)
    if session:
        from .pg_usage import update_rollup
        evidence=update_rollup(store,task_id)
        if not evidence['complete']:return {'price':None,'price_usd':None,'evidence':evidence}
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
    raise HTTPException(404,"session task binding unavailable")


def _task_audit_totals(task_id:str):
    priced_by_phase={}
    for event in store.events(task_id):
        if event.get("kind")=="price":priced_by_phase[event.get("phase_key")]=event
    priced=list(priced_by_phase.values())
    session=_recorded_engine_session(task_id)
    interview_usage=[]
    if session:
        from .generator_documents import GeneratorDocuments
        usage=GeneratorDocuments.owner_reader(store,task_id).read('interview/usage.jsonl')
        if usage is not None:interview_usage=[json.loads(line) for line in usage.decode().splitlines() if line.strip()]
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
    return totals

@app.get("/api/tasks/{task_id}/audit")
def task_audit(task_id: str, request: Request):
    if not store.task(task_id):raise HTTPException(404,"task not found")
    totals=_task_audit_totals(task_id)
    entries=store.audit_entries(task_id)
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
    with tempfile.TemporaryDirectory(prefix='vmodel-import-') as temporary:
        root=Path(temporary);dest=root/'usage.jsonl';total=0
        with dest.open('wb') as fh:
            while chunk:=await usage_file.read(1024*1024):
                total+=len(chunk)
                if total>20_000_000:raise HTTPException(413,{'error':'The selected file is too large.','cause':'The 20 MB upload safety limit was exceeded.','next':'Choose the original usage.jsonl or archive the oversized recording.'})
                fh.write(chunk)
        try:return simulation.import_run(root,title)
        except ImportValidationError as e:raise HTTPException(400,{'error':str(e),'cause':e.cause,'next':e.next_action})

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



def _read_stack_decisions(task_id:str):
    """The architecture gate's open research_required slots (written by spec-generator), or None."""
    if not _recorded_engine_session(task_id):return None
    try:raw=store.read_artifact_bytes(task_id,'architecture','architecture.decisions.json')
    except KeyError:return None
    try:data=json.loads(raw)
    except ValueError:raise HTTPException(409,'architecture decisions are unreadable')
    if not isinstance(data,dict) or not isinstance(data.get('slots'),list) or not isinstance(data.get('revision'),str):raise HTTPException(409,'architecture decisions are malformed')
    return data

def _open_stack_slots(data)->list[str]:
    return [s['key'] for s in (data or {}).get('slots',[]) if not s.get('founder_decision') and not s.get('technical_choice')]





def _lineage_gate_settled(task_id:str,phase_key:str)->None:
    from .generator_documents import transition_gate_lineage
    return transition_gate_lineage(store,task_id,phase_key,settled=True)

def _lineage_gate_redo_invalidate(task_id:str,phase_key:str)->None:
    from .generator_documents import transition_gate_lineage
    return transition_gate_lineage(store,task_id,phase_key,settled=False)


def _guard_task_file_operation(function):
    # Sync endpoints/helpers only. Never hold a DB transaction across async
    # provider dispatch or gate continuation. Local adapter is unchanged.
    from functools import wraps
    @wraps(function)
    def guarded(*args,**kwargs):
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
    artifact = None
    full = json.loads(store.read_artifact_bytes(task_id,'architecture','architecture.json'))
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
    from .generator_documents import commit_gate_documents
    commit_gate_documents(store,task_id,'architecture',{'architecture.json':json.dumps(full,indent=2).encode(),'architecture.decisions.json':json.dumps(companion,indent=2).encode()})
    return


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
        from vmodel_contracts.semantic_review import SemanticReview
        try:semantic=SemanticReview.model_validate_json(store.read_artifact_bytes(task_id,'srs','srs.semantic-review.json')).model_dump(mode='json')
        except KeyError:pass
        if semantic and semantic['session_id']!=_recorded_engine_session(task_id):raise HTTPException(409,'semantic review belongs to another session')
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
    return {'review':review,'historical':review['revision']!=current['revision'],'actionable':review['revision']==current['revision'] and gate['state']=='waiting',
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
    store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
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
    from .generator_documents import commit_gate_documents
    full={**full,**resolved}
    try:commit_gate_documents(store,task_id,'srs',{'srs.semantic-review.json':review.model_dump_json(indent=2).encode(),'srs.json':json.dumps(full,indent=2).encode()})
    except ValueError as exc:raise HTTPException(409,str(exc))
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
    store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE', (task_id,)).fetchone()
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
    artifact=None
    full=json.loads(store.read_artifact_bytes(task_id,'architecture','architecture.json'))
    for entry in full.get('stack_entry_dispositions') or []:
        if entry.get('key') in made:entry['founder_decision']=made[entry['key']]
    companion={**data,'slots':[{**s,'founder_decision':made.get(s['key'],s.get('founder_decision')),
                               'prefill':None if s['key'] in made else s.get('prefill')} for s in data['slots']]}
    from .generator_documents import commit_gate_documents
    commit_gate_documents(store,task_id,'architecture',{'architecture.decisions.json':json.dumps(companion,indent=2).encode(),'architecture.json':json.dumps(full,indent=2).encode()})
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
@_guard_task_file_operation
def _decide_gate_sync(task_id,gate_key,req,a):
    if not store.task(task_id):raise HTTPException(404,'task not found')
    if not cloud_execution_admitted():raise HTTPException(503,'Cloud admission and lifecycle validation pending')

    try:
        store._execute_native('SELECT task_id FROM tasks WHERE task_id=%s FOR UPDATE',(task_id,)).fetchone()
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
            if not evidence or not any(a.get("valid") is not False for a in evidence):
                raise ValueError("No current valid artifact is available to review at this gate")
        if pending['after_phase']=='srs' and req.action!='redo' and _recorded_engine_session(task_id):
            # Every srs run produces its semantic review (spec-generator graph node_srs),
            # so the file is required approval evidence for a session-recorded task.
            # Fail closed when it is absent - never skip the reviewed-write check silently.
            from vmodel_contracts.semantic_review import SemanticReview
            try:semantic=SemanticReview.model_validate_json(store.read_artifact_bytes(task_id,'srs','srs.semantic-review.json'))
            except KeyError:raise ValueError('Required approval evidence is missing: srs.semantic-review.json is not published for this execution')
            resolved=semantic.resolved_candidate(lambda candidate:None)
            canonical=json.loads(task_artifact_view(task_id,'srs','srs.json')['raw_content'])
            if any(canonical.get(k)!=v for k,v in resolved.items()):raise ValueError('Reviewed SRS write is incomplete; save the item decisions again before approving')
        if pending['after_phase']=='architecture' and req.action!='redo':
            # research_required slots in the canonical artifact mean the generator
            # wrote the founder-decision companion; it is required approval evidence.
            try:
                full=json.loads(store.read_artifact_bytes(task_id,'architecture','architecture.json'))
                research_required=[d for d in full.get('stack_entry_dispositions') or [] if str(d.get('status'))=='research_required']
            except HTTPException:
                research_required=[]
            decisions=_read_stack_decisions(task_id)
            if research_required and decisions is None:
                raise ValueError('Required approval evidence is missing: this architecture has research-required stack slots but architecture.decisions.json is not published in PostgreSQL. The gate does not approve without the founder-decision record.')
            still_open=_open_stack_slots(decisions)
            if still_open:raise ValueError(f'{len(still_open)} architecture decision(s) still open; decide each one before approving')
        from .phases import PHASE_BY_KEY, successor
        target=pending["after_phase"] if req.action=="redo" else (successor(pending["after_phase"]).key if successor(pending["after_phase"]) else pending["after_phase"])
        if req.model_route and PHASE_BY_KEY[target].executor!="generator":
            raise ValueError("This gate has no generator phase for a model override")
        if (req.model_route) and (req.model_route not in local_route.managed_routes()):
            raise ValueError("That model route is not available on this server.")
        selected=model_route_lib.route_value(req.model_route,req.model) if req.model_route else None
        if PHASE_BY_KEY[target].executor=="generator" and not selected:
            model_route_lib.task_default_model(store,task_id,PHASE_BY_KEY[target].generator_stage)
        if pending['after_phase']=='architecture' and pending['state']=='waiting' and req.action=='approve':
            _use_architecture_defaults(task_id)
        gate,job_id,target_phase=store.prepare_gate_dispatch(task_id,gate_key,req.action,actor=a.label,
            model=selected,comment=req.comment,review_revision=req.review_revision,
            selected_option=req.selected_option,edited_text=req.edited_text,steer=req.steer,
            expected_revision=req.expected_revision)
        if req.action=='approve':_lineage_gate_settled(task_id,pending['after_phase'])
        elif req.action=='redo':_lineage_gate_redo_invalidate(task_id,pending['after_phase'])
        return gate,job_id,target_phase,selected
    except KeyError:raise HTTPException(404,"gate not found")
    except ValueError as e:raise HTTPException(400,{"error":str(e),"cause":"The gate decision is incomplete.","next":"Choose, edit, or explain what the redo should change."})


@app.post("/api/tasks/{task_id}/gates/{gate_key}/decide")
async def decide_gate(task_id:str,gate_key:str,req:GateDecision,request:Request,a:auth.Actor=Depends(auth.require_actor)):
    # Finish the gate, document defaults, lineage and queue transaction before
    # dispatch. No transaction or task lock spans a provider/network await.
    gate,job_id,phase,selected=_decide_gate_sync(task_id,gate_key,req,a)
    if job_id:
        from .tenant_jobs import dispatch_phase
        await dispatch_phase(store,orchestrator,task_id,phase,queued_job_id=job_id)
    return gate

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
    from .generator_documents import GeneratorDocuments
    try:raw=GeneratorDocuments.owner_reader(store,task_id).read('interview/categorization-shadow.jsonl')
    except TenantAccessDenied:raise
    except Exception:
        raise HTTPException(503,'Classification evidence is unavailable') from None
    if raw is None:
        out['reason']='no shadow evidence';return out
    turns=_qa_turns(store.events(task_id,limit=10000))
    try:
        if len(raw)>8*1024*1024:raise ValueError('bounded classification evidence required')
        def invalid_number(value):raise ValueError('finite classification values required')
        records=[json.loads(line,parse_constant=invalid_number) for line in raw.decode('utf-8').splitlines() if line.strip()]
        if len(records)>10000 or any(not isinstance(row,dict) for row in records):raise ValueError('bounded classification records required')
        for record in records:
            if not isinstance(record.get('slot'),str):raise ValueError('classification slot required')
            if not isinstance(record.get('input',{}),dict):raise ValueError('classification input required')
            if not isinstance(record.get('input',{}).get('state',''),str):raise ValueError('classification state required')
            if record.get('timestamp') is not None and not isinstance(record['timestamp'],str):raise ValueError('classification time required')
            for side in ('baseline','jev'):
                value=record.get(side,{})
                if not isinstance(value,dict) or not isinstance(value.get('verdict',{}),dict):raise ValueError('classification verdict required')
                if value.get('status') is not None and not isinstance(value['status'],str):raise ValueError('classification status required')
    except (ValueError,UnicodeError,RecursionError):
        raise HTTPException(503,'Classification evidence is unreadable') from None
    scan_bytes=projected_bytes=0
    entry_sizes={}
    for rec in records:
        slot=rec.get("slot")
        if slot not in ("intent","coverage"): continue
        state=str((rec.get("input") or {}).get("state") or "")
        if not state: continue
        scan_bytes+=len(state)*len(turns)
        if scan_bytes>64*1024*1024:raise HTTPException(503,'Classification evidence exceeds read limits')
        for t in turns:
            if t["q"] in state and t["a"] in state:
                aref=_content_ref(t["q"]+"\n"+t["a"])
                entry=out["answers"].setdefault(aref,{})
                base,jev=rec.get("baseline") or {},rec.get("jev") or {}
                differed=(base.get("status")=="ok" and jev.get("status")=="ok" and base.get("verdict")!=jev.get("verdict"))
                projection={"jev":jev.get("verdict") or {},"baseline":base.get("verdict") or {},
                             "jev_status":jev.get("status"),"differed":differed,
                             "ts":rec.get("timestamp")}
                key=(aref,slot)
                size=len(json.dumps(projection,ensure_ascii=True).encode())+len(aref)+len(slot)+16
                projected_bytes+=size-entry_sizes.get(key,0)
                if projected_bytes>8*1024*1024:raise HTTPException(503,'Classification evidence exceeds read limits')
                entry_sizes[key]=size
                entry[slot]=projection
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
    if (a.admin):
        from .process_logs import operator_view
        return operator_view(_operator_reader(),store,minutes,task_id=task_id if isinstance(task_id,str) else None)
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

@app.get('/api/tasks/{task_id}/log-window')
def task_log_window(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    if not a.admin:raise HTTPException(403,'operator log access required')
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    from .process_logs import task_window
    return task_window(_operator_reader(),store,task_id,task.get('engine_session_id'),float(task['created_at']))

@app.get("/health")
def health():
    return {"ok":True,"runtime_id":RUNTIME_VERSION["runtime_id"],"repositories":RUNTIME_VERSION["repositories"],"built_at":RUNTIME_VERSION.get("built_at")}

@app.post('/api/tasks/{task_id}/packages')
def create_final_package(task_id:str,a:auth.Actor=Depends(auth.require_actor)):
    _deny_test_product_export(task_id)
    task=store.task(task_id)
    if not task:raise HTTPException(404,'task not found')
    try:
        result=store.create_final_package(task_id)
        return {**result,'url':f"/api/tasks/{task_id}/packages/{result['package_id']}?generation={result['generation']}",'status':'ready'}
    except (ValueError,KeyError,OSError,__import__('subprocess').TimeoutExpired):
        raise HTTPException(409,{'error':'Final package is blocked','next':'Finish approval and publish the complete reviewed final revision before exporting. No model work was started.'})


@app.get('/api/tasks/{task_id}/packages/{package_id}')
def download_final_package(task_id:str,package_id:str,generation:int,a:auth.Actor=Depends(auth.require_actor)):
    _deny_test_product_export(task_id)
    if not __import__('re').fullmatch(r'[a-f0-9]{64}',package_id) or generation<1:raise HTTPException(404,'package not found')
    task=store.task(task_id)
    if not task:
        activity("task.export.refused","Package download refused: no such task",task_id=task_id,export="package",actor=a.label,reason="not_found")
        raise HTTPException(404,'task not found')
    from fastapi.responses import Response
    try:raw=store.final_package(task_id,generation,package_id)
    except KeyError:raise HTTPException(404,'package not found')
    except (ValueError,OSError):raise HTTPException(409,'Package verification failed; no download was served')
    return Response(raw,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="coding-agent-{task_id}-g{generation}.zip"','Cache-Control':'private, no-store'})


@app.get('/api/worker-jobs/recovery')
def recoverable_worker_jobs(a:auth.Actor=Depends(auth.require_actor)):
    return {'jobs':store.recoverable_worker_jobs(),'execution_enabled':cloud_execution_admitted(),
            'reason':'Admission and full cloud lifecycle tests pending'}

class WorkerResumeRequest(BaseModel):
    job_id:str=Field(min_length=1,max_length=128)
    phase:str=Field(min_length=1,max_length=64)

@app.post('/api/tasks/{task_id}/worker-resume')
async def resume_cloud_worker(task_id:str,body:WorkerResumeRequest,a:auth.Actor=Depends(auth.require_actor)):
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
    """Reviewed runtime readiness; per-job owner and money admission remain mandatory."""
    from . import cloud_execution
    return cloud_execution.admitted()


class WorkerJobStopRequest(BaseModel):
    confirm:bool=False
    hide:bool=True


@app.post('/api/operations/worker-jobs/{job_id}/stop')
def operator_worker_job_stop(job_id:str,body:WorkerJobStopRequest,a:auth.Actor=Depends(auth.require_actor)):
    _require_account_debug()
    try:result=store.stop_hide_worker_task(job_id,confirm=body.confirm,actor=a.label,hide=body.hide)
    except KeyError:raise HTTPException(404,'Owned worker job not found')
    except ValueError as exc:raise HTTPException(422,str(exc))
    # Database Stop/generation fencing is authoritative even if the engine is
    # gone. Notification is best effort and cannot turn cleanup into a refusal.
    notified=False
    try:
        engine_client.stop(result['session_id'])
        notified=True
    except Exception:
        activity('task.stop.notification_unverified','Task hidden with durable Stop; engine notification unverified',task_id=result['task_id'])
    return JSONResponse(result|{'engine_notification_verified':notified},headers={'Cache-Control':'no-store'})


class ForceStopAllRequest(BaseModel):
    model_config={'extra':'forbid'}
    fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmation:str=Field(min_length=1,max_length=4096)


@app.get('/api/operations/force-stop-plan')
def owner_force_stop_plan(a:auth.Actor=Depends(auth.require_actor)):
    _require_account_debug()
    raise HTTPException(410,'Force job closure was retired. Use normal Stop or the reviewed owner preview reset after stopping producers; accounting remains unresolved.')


@app.post('/api/operations/force-stop-all')
def owner_force_stop_all(body:ForceStopAllRequest,a:auth.Actor=Depends(auth.require_actor)):
    _require_account_debug()
    raise HTTPException(410,'Force job closure was retired. No job or accounting record changed. Use the reviewed owner preview reset after stopping producers.')


@app.get('/api/operations/active-jobs')
def owner_active_jobs(limit:int=50,before_updated_at:float|None=None,before_job_id:str|None=None,a:auth.Actor=Depends(auth.require_actor)):
    _require_account_debug()
    if (before_updated_at is None)!=(before_job_id is None):raise HTTPException(422,'complete jobs cursor required')
    before=None if before_job_id is None else {'updated_at':before_updated_at,'job_id':before_job_id}
    try:result=store.owner_active_jobs(limit=limit,before=before)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@app.get('/api/admin/operations/orphaned-tasks')
def admin_orphaned_tasks(a:auth.Actor=Depends(auth.require_admin)):
    from .recovery import orphan_review_items
    return {'tasks':orphan_review_items(store),'scope':'current_authorized_workspace','auto_restart':False}


@app.get('/api/admin/operations/workspace')
def admin_operations_workspace(a:auth.Actor=Depends(auth.require_admin)):
    return store.operator_workspace_snapshot()


@app.get('/api/debug/evidence')
def debug_evidence(task_id:str|None=Query(None,max_length=240),a:auth.Actor=Depends(auth.require_actor)):
    if not a.admin:raise HTTPException(403,'operator access required')
    from .process_logs import selected
    with _operator_reader()._read(store,'logs.read') as (_,authority):pass
    result=selected().records(stream='diagnostics',task_id=task_id,limit=500)
    rows=result['records']
    return {'available':not result['unreadable'],'availability':'unreadable' if result['unreadable'] else 'available',
        'text':'\n'.join(row['text'] for row in rows),'count':len(rows),'truncated':result['truncated'],
        'unreadable':result['unreadable'],'task_id':task_id,'scope':'operator diagnostic evidence',
        'storage':'ephemeral bounded server files'}


@app.get('/api/debug/log-durability')
def debug_log_durability(a:auth.Actor=Depends(auth.require_actor)):
    if not a.admin:raise HTTPException(403,'operator access required')
    from .log_durability import snapshot
    from .process_logs import writer_status
    return {**snapshot(),'writer':writer_status()}


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
    metrics=_operator_metrics(limit=8)
    snap=run_slots.snapshot(metrics=metrics);snap['text']=run_slots.snapshot_text(snap)
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
    from .process_logs import operator_view
    import io,zipfile
    from datetime import datetime,timezone
    result=operator_view(_operator_reader(),store,minutes,download=True)
    name='requi-server-log-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')+'.zip'
    body='Requi.re server log\nSource: ephemeral bounded server files; loss on restart is expected.\nScrubbed; exception messages cut; structured frames and unstructured engine stdout omitted.\nTruncated: '+str(result['truncated'])+'\n\n'+result['text']+'\n'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name[:-4]+'.txt',body)
    data=buffer.getvalue();lines=result['count']
    activity('ops.log_download','Operator downloaded the server log',actor=a.label,minutes=minutes,lines=lines)
    return Response(content=data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="%s"'%name,'Cache-Control':'no-store'})


@app.get('/api/admin/operations/stops')
def admin_operations_stops(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: every run stop, read through the read-only operator database role. The database registry decides, not this check alone.
    try:
        return _operator_reader().stops(store)
    except Exception as exc:
        activity('ops.stops_unavailable','Operator stop list could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The stop list is not switched on yet.','cause':'The database has not been set up for operator reads.',
            'next':'Run supabase/migrations/20261020130000_operator_stop_audit.sql in the Supabase SQL editor, then reload.'})


@app.get('/api/admin/operations/attempt-releases')
def admin_operations_attempt_releases(limit:int=200,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: reservation releases and failed releases, read through the
    # read-only operator database role. The database registry decides, not this
    # check alone.
    try:
        return _operator_reader().attempt_releases(store,limit=limit)
    except Exception as exc:
        activity('ops.attempt_releases_unavailable','Operator reservation-release list could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The reservation-release list is not switched on yet.','cause':'The database has not been set up for operator reads.',
            'next':'Apply the operator read role and registry used by the stop list, then reload.'})


@app.get('/api/admin/operations/spend')
def admin_operations_spend(a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: credits and vendor expense per account. Users never see this; no link in the user UI.
    try:
        return _operator_reader().spend(store)
    except Exception as exc:
        activity('ops.spend_unavailable','Operator credits view could not be read',actor=a.label,error=type(exc).__name__)
        raise HTTPException(503,{'error':'The credits page is not switched on yet.','cause':'The database has not been set up for operator reads or the credit history.',
            'next':'Run supabase/migrations/20261020130000_operator_stop_audit.sql and 20261020080000_credit_ledger.sql in the Supabase SQL editor, then reload.'})


def _grant_unavailable(a,exc):
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
    try:return _operator_reader().grants(store)
    except Exception as exc:raise _grant_unavailable(a,exc)

@app.post('/api/admin/operations/grant-preview')
def admin_operations_grant_preview(body:GrantRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: what a grant would do. Writes nothing.
    try:return _operator_reader().grant_preview(store,body.email,body.credits)
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception as exc:
        import psycopg
        if isinstance(exc,psycopg.errors.RaiseException):raise HTTPException(400,exc.diag.message_primary or 'The grant was refused.')
        raise _grant_unavailable(a,exc)

@app.post('/api/admin/operations/grant')
def admin_operations_grant(body:GrantRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: give credits. Needs the typed word yes. The database checks the operator registry again and refuses a same-day repeat.
    if body.confirm!='yes':raise HTTPException(400,'Type "yes" to confirm the grant.')
    try:out=_operator_reader().grant_apply(store,body.email,body.credits,body.reason)
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
    return _operator_metrics(task_id=task_id or None,limit=2000 if task_id else 100)


class AdminStopRunRequest(BaseModel):
    task_id: str = Field(min_length=1,max_length=120)
    confirm: bool = False

@app.post('/api/admin/operations/stop-run')
def admin_stop_run(body:AdminStopRunRequest,a:auth.Actor=Depends(auth.require_admin)):
    # Operator only: stop any running phase on this server. The owner is told an operator stopped it, and the audit entry is written in their workspace when the run ends.
    if config.RUN_ISOLATION!='process':raise HTTPException(409,{'error':'A run cannot be stopped on this server yet.','cause':'Runs here do not have their own process.','next':'Wait for the run to finish.'})
    from . import run_slots
    if not body.confirm:
        return {'status':'preview','running':run_slots.is_running(body.task_id),'message':'Stop the running phase of task %s? Its owner is told an operator stopped it, and it is recorded in the audit log.'%body.task_id}
    if not run_slots.request_stop(body.task_id,'operator',a.label):
        raise HTTPException(409,{'error':'Nothing is running for that task.','cause':'The phase has already finished or has not started.','next':'Reload the capacity view.'})
    activity('run.stop_requested','Operator asked to stop a run',task_id=body.task_id,actor=a.label)
    return {'status':'stopping','message':'Stopping. This takes a few seconds.'}


def _operator_metrics(**kwargs):
    try:return _operator_reader().metrics(store,**kwargs)
    except (HTTPException,TenantAccessDenied):raise
    except Exception:
        raise HTTPException(503,{'error':'Resource metrics are temporarily unavailable.',
            'next':'Retry after the metrics database and operator registry are available.'}) from None


def _operator_reader():
    reader=getattr(app.state,'operator_read_store',None)
    if reader is None:
        raise HTTPException(503,'Verified sole-operator read registry is not configured. Set REQUI_OPERATOR_SUBJECT, REQUI_OPERATOR_REGISTRY_VERSION and REQUI_OPERATOR_CAPABILITIES in protected server settings to match the reviewed database registry; restart is required. No permission is inferred from admin status.')
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
    from .pricing import retail_rates
    return store.own_ledger(retail_rates().usd_per_credit)


class ComputeReleaseRequest(BaseModel):
    confirm: bool = False

@app.post('/api/account/compute-release')
def account_compute_release(body:ComputeReleaseRequest,a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer releases their own stuck compute records. Only records whose job never reached a provider; each is audited.
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
    from .pricing import retail_rates
    out=dict(store.own_credit_balance(retail_rates().usd_per_credit))
    try:out['last_topup']=store.own_last_topup()
    except Exception:
        log.warning('last top-up unavailable',exc_info=True);out['last_topup']=None   # the balance itself must still show
    return out


@app.get('/api/account/credit-history')
def account_credit_history(a:auth.Actor=Depends(auth.require_actor)):
    # The signed-in customer's own credit changes, read-only, for the Debug dialog. Local mode has no prepaid accounts.
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
    reader=_operator_reader()
    reason=reader.usage_setup_reason(store)
    if reason:
        return {'availability':'unavailable','category':'permission_missing','required_capability':'usage.read',
            'reason':reason,'next':'Apply the reviewed standard operator migration and save standard operator settings; restart after active work is quiescent.'}
    # No detailed setup disclosures to another user or unverified operator.
    try:return reader.task_usage(store,task_id)
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


from .model_pin_api import install as install_model_pin_routes
def _activate_reviewed_pins():
    from . import cloud_runtime
    cloud_runtime._failure=None  # explicit operator configuration repair retries startup checks
    if cloud_execution_admitted():cloud_runtime.install(store,os.environ,_set_engine_client,watch_vendor=True)
install_model_pin_routes(app,store,_operator_reader,_activate_reviewed_pins)


@app.get('/api/debug/collection')
def debug_collection(task_id:str|None=Query(None,max_length=240),a:auth.Actor=Depends(auth.require_actor)):
    from .debug_collection import collect
    import sys
    return collect(sys.modules[__name__],a,task_id)


@app.get('/api/operator/backoffice-access')
def operator_backoffice_access(a:auth.Actor=Depends(auth.require_actor)):
    from .backoffice_access import effective,PURPOSES
    try:return JSONResponse(effective(_operator_reader(),store),headers={'Cache-Control':'no-store'})
    except HTTPException:return JSONResponse({'capabilities':{c:False for c in PURPOSES},'checks':{c:{'state':'denied','reason':'Running delivery process has no configured operator reader. Review protected REQUI_OPERATOR_SUBJECT, REQUI_OPERATOR_REGISTRY_VERSION and REQUI_OPERATOR_CAPABILITIES; restart delivery after approved settings changes.'} for c in PURPOSES},'purposes':PURPOSES,'paid_activation':False,'write_off':False},headers={'Cache-Control':'no-store'})


@app.get('/api/operator/audit-events')
def operator_audit_events(before:int|None=None,limit:int=50,event:str|None=None,a:auth.Actor=Depends(auth.require_admin)):
    from .audit_view import list_rows
    try:return JSONResponse(list_rows(_operator_reader(),store,owner=a.id,before=before,limit=limit,event=event),headers={'Cache-Control':'no-store'})
    except ValueError as exc:raise HTTPException(422,str(exc))

class AuditResolutionRequest(BaseModel):
    model_config={'extra':'forbid'}
    action:Literal['hide','cancel','release']
    confirm:bool=False
    expected_attempt_state:str|None=None

@app.post('/api/operations/audit-events/{audit_id}/resolve')
def resolve_audit_event(audit_id:int,body:AuditResolutionRequest,a:auth.Actor=Depends(auth.require_admin)):
    _require_account_debug()
    if not body.confirm:raise HTTPException(422,'Explicit confirmation required')
    # Fresh logs authority, then drop back to normal current-owner store scope.
    with _operator_reader()._read(store,'logs.read'):pass
    from .attempt_ledger import AdmissionDenied
    try:result=store.resolve_audit_event(audit_id,body.action,body.expected_attempt_state)
    except KeyError:raise HTTPException(404,'Owned audit record not found')
    except (ValueError,AdmissionDenied) as exc:raise HTTPException(409,str(exc))
    return JSONResponse(result,headers={'Cache-Control':'no-store'})
