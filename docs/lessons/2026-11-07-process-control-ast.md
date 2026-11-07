# 2026-11-07 — The process-control check went stale for five weeks and nobody saw

## What happened
`scripts/ci/check-process-control.py` was created 2026-09-21 (8fc49f8) to fail
when the HTTP interview control path branches on engine prose. It sliced
app.py into three string ranges between `def ...` markers and scanned those
ranges for banned tokens. On 2026-09-29 the JEV-D7 refactor (ea22cd8) removed
`def _task_ui_state`, one of the markers, and the script died with
`ValueError: substring not found` on every run from then on. The script had
never been wired into any workflow, so no surface ran it, and the check
silently protected nothing for five weeks.

## Root cause
String markers couple the check to helper names that refactors freely change,
and an unwired check has no surface to fail on. Both had to be true for the
silence: a brittle extraction AND no gate step.

## Fix
The check now parses app.py and finds the three control routes by decorator -
`@app.post("/api/tasks/{task_id}/start")`, `@app.post("/api/tasks/{task_id}/answer")`,
`@app.get("/api/tasks/{task_id}")` - which are the API contract and do not
move in helper refactors. A route that is missing or re-routed is a loud
error, never a silent pass. engine_client.py and engine_projection.py are
still scanned whole for banned tokens. The script now runs as a step in the
every-PR gate job, and `tests/test_ci_fast_gate.py` asserts that step exists
so the wiring itself cannot regress quietly.

## Trade-off accepted
Coverage is now precise instead of accidental: exactly the three route
functions are scanned, not whatever helpers happened to sit between two
string markers (the old task-GET range had grown to sweep in the
decision-review endpoints and debug helpers). Prose control in shared
helpers is still caught by the whole-file scans of the engine modules.

## Rule
A check that is not wired into the every-PR gate does not exist, and a check
keyed to names a refactor can rename is already broken.
