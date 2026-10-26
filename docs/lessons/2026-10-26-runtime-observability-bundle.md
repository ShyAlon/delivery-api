# 2026-10-26 — Console quieting, build time, UI action logging, and one-events-read polls

## Why bundled
Shy asked (2026-10-26 16:53-16:54) for one PR: quiet routine request lines, image
build time in Debug + ZIP, console logging at shared button/Enter points, a
per-request DB cost line when slow, the managed-route label, and removal of
provably redundant DB reads in polled handlers.

## What changed and why
- The terminal was drowning in routine `http.request` 200 lines and uvicorn
  access lines, so real signals (a 5s poll) were invisible. Routine successes
  now go to run.log only (Debug evidence/ZIP unchanged); non-2xx, slow
  (>=1000ms) and errors log WARNING to console with the request's DB cost
  (db_queries/db_ms from the existing RequestStats). uvicorn's access log is
  off: the middleware records a strictly richer line for every request.
- The 7.2 ZIP showed every polled GET costing 4-13s with no measured cause.
  Read-from-code: GET /api/tasks/{id} read store.events THREE times per request
  (twice in the `starting` comprehension, once in the response). Now one read;
  a call-count test pins it. Other candidates (diagnostics re-reading through
  get_task, local-mode runner reads) are listed for review, not touched.
- `REQUI_BUILD_AT` (UTC ISO-8601) is baked by both build scripts and surfaced in
  /health and the runtime version dict, so the Debug dialog and ZIP show image
  build time next to the commits.
- No shared button/in-flight helper existed; buttons set `.disabled` ad hoc.
  New `ui-action.js` logs names-only records (ui.inflight / ui.disabled /
  ui.enter) at the shared points: auth submit, start interview, answer composer
  guard + disables + Enter resolution, dialog Enter resolution. His console now
  shows WHY a button stayed disabled or an Enter went nowhere.
- The managed route label now says what it means: "Managed by Requi.re · billed
  to your credits".

## Lesson
Silence routine success at the console boundary, never at the evidence
boundary: the file log keeps every line, the ZIP loses nothing, and the slow
lines that remain on screen carry their own DB cost so the next "too many DB
calls" question starts from numbers, not guesses.
