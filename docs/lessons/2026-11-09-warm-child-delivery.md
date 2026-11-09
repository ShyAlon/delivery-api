# 2026-11-09: Warm child delivery - per-op channels ride the frame, not the park

## Problem

Every interview answer paid a full cold-child lifecycle (spawn, claim,
restore, compile, reap): ~26s attach + ~9s reap around a ~33s model turn.
The engine half (interview-engine #96) made the session holdable; the
delivery parent still had no way to keep a child alive between answers.

## Root cause

`ChildSupervisor.run()` was one-shot: spawn, one operation, EOF/exit
verification, reap - with the provider-broker and compute channels scoped
to that single operation's handler. `child_entry.main()` executed exactly
one operation and closed everything in `finally`.

## Fix

- `child_supervisor.py`: `run()` split into `spawn` / `run_operation` /
  `reap` (run() composes them, behavior unchanged). `park=True` returns at
  the terminal event without EOF/exit checks; a forged trailing frame is
  rejected by the next operation's identity + cumulative-sequence check.
  `stop_parked` / `close_parked` end an idle parked child with a verified
  clean exit. `finish_terminal` holds the EOF/exit verification once.
- `child_entry.py`: a warm child (private config `warm` + interview
  answer/advance/route) keeps its claimed session (engine `WarmChildStep`)
  and serves follow-up operations in a loop.
- `child_interview.py`: a parked registry keyed by session id. Park only
  after a verified completed operation on a session waiting for input.
  Warm dispatches authorize the broker at the HELD lease epoch (not +1).
  Recycle on route switch, stop (fenced then reaped), completion, 600s
  idle, or any error. UI states: 'Warming up a fresh session' at spawn;
  'Reusing an existing session' (started/completed around the handoff) at
  warm dispatch.

## Key decision: per-op channels via SCM_RIGHTS

Two channel designs were rejected with evidence: keeping the broker channel
open across ops dies to `provider_broker_channel`'s permanent-poison
semantics (a provider refusal kills the channel by design) and to
`ComputeClient` closing on 'finish' (one-shot by protocol). Instead each
follow-up frame is one sendmsg carrying the JSON operation config plus
that operation's fresh broker/compute descriptors. Every piece of per-op
money/quiescence machinery (broker, authority, journal, accounting,
channel quiescence) is constructed and torn down per operation EXACTLY as
in the cold path; the only things that persist are the process, the
stream, the progress channel, and the lease-fenced engine session.

## Evidence

- Cold path unchanged: test_child_supervisor.py, test_child_execution_contract,
  test_child_progress_wake, test_child_engine_timings (70 passed) plus the
  interview/answer/crash/cost/pool files (failure sets byte-identical to
  clean origin/main via worktree diff - all the known sandbox real-child
  environment class).
- Warm supervisor: tests/test_child_supervisor_warm.py 5 passed - same pid
  across operations, gifted descriptor live, cumulative sequence, forged
  trailing frame rejected, slot held until reap, stop/close parked exit 0.
- Framing: tests/test_child_warm_frame.py 3 passed.

## Lesson

When a persistent thing needs per-operation resources, pass the resources
through the channel you already have (SCM_RIGHTS on the existing socket)
instead of widening the lifetimes of machinery designed to be one-shot.
Lifetime widening smuggles in poisoning and settlement bugs; descriptor
passing keeps every guarantee where it was proven.
