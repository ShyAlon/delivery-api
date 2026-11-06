# 2026-11-06: Task detail re-renders must fence on the render revision, not only the page epoch

## Problem
The task detail page polled GET /api/tasks/{id} and /phase-output every 60s each,
and the observed rate grew in whole-number steps (3/min to 5/min) across sleep/wake.
In-page re-renders (`snapshotRefresh` diff, stop flows) call `detail(id)` without
bumping the page epoch, so overlapping renders share `pageStale(at)` and an older
render that finished last committed its DOM and overwrote `taskDispose`, leaking the
newer render's 60s timers and EventSource. Every leaked render kept polling forever,
and its dead stream kept feeding events into `queueSnapshot`, snowballing re-renders.

## Fix
- `detail()` now compares `revision!==detailRevision` at the commit point, alongside
  `pageStale(at)`: only the newest render commits DOM, timers and `taskDispose`.
- `snapshotRefresh` and the stop-status poller fence on the same revision so a stale
  render can neither start a re-render nor dispose the current one.
- One safety scheduler replaces the fixed 60s interval: setTimeout chain, 120s while
  the progress stream is connected, 60s while it is degraded; it skips ticks while the
  tab is hidden or an answer send is in flight, and refreshes once on return to visible.

## Lesson
`pageStale` fences navigation, not same-page re-renders. Any render that can overlap
itself needs a monotonic revision checked at commit time, and every path that can
dispose or replace a render must check it too. A fixed-cadence "safety" poll is a
read multiplier: its cadence must follow the health of the primary push channel.
