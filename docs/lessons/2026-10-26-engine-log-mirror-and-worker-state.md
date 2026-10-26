# 2026-10-26 — The engine log file must mirror the engine that actually serves

## Incident
Task 9dd349f2ef25 stalled at 07:54:46Z and the everything ZIP's "engine process"
section showed only four uvicorn startup lines - no warnings, no heartbeats. The
obvious reading was "the tee is broken".

## Root cause
The tee (scripts/docker/full_entry.py) is fine. The deployment hosts the engine
IN-PROCESS (cloud_runtime -> InProcessInterviewHost): the real engine stream is
the delivery process's own logging, captured by the in-memory server_log_buffer
(30-minute, 2000-line window). The uvicorn engine subprocess on 8010 sits idle -
nothing routes to it - so engine.log legitimately contains only its startup
lines. Two consequences: the Debug engine section was misleading, and engine
evidence older than the buffer window (or lost at a restart) was gone for good.

## Fix (no behavior change)
- engine_log.install_mirror(): a logging handler on the interview_engine logger
  (INFO) that appends scrubbed, tee-stamped lines to the same bounded engine.log
  (rotates once at 2 MB). Installed by cloud_runtime.install exactly when the
  engine is hosted in-process; never fatal (first write error disables it).
- The handler raises the logger to INFO so heartbeat lifecycle lines reach the
  file; the customer-visible buffer stays WARNING-only.
- /api/tasks/{id}/diagnostics gains a worker_state section (new store read
  task_worker_state): the worker_jobs + provider_attempts rows the Stop-settle
  gates wait on, owner-scoped, payload columns excluded. A parked interview's
  forever-running job is now visible without a database console.

## Lesson
When the process topology changes (subprocess -> in-process host), every
artifact built on the old topology becomes a lie that still looks healthy -
four clean startup lines. Name the producing process in the artifact, and
mirror the real stream into the file operators actually download.
