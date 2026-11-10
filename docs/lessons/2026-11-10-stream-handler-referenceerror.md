# 2026-11-10: every live progress event threw in the browser

Problem: the task page showed a bare "Processing your answer...", a permanent
"Progress connection lost", and an elapsed time of 57 hours, although the server
had emitted granular stage events (ids 730-756 for task 7f3b86686274).

Cause: `answerInFlight` and `answerShadowMs` in `web/task.js` were module-level
functions that read `t` and `shadowKey`, locals of `detail()`. Optional chaining
(`t?.x`) does not guard an undeclared name, so each SSE `progress` event and each
reconnect refresh threw ReferenceError. Commit 6f8ab386 (`[skip ci]`) introduced
it and no test ran before it shipped.

Fix: module-level `liveTask` / `liveShadowKey` set by `detail()`; every in-flight
surface (phase line, live indicator, progress scope) is refined from the same
observed stage; heartbeats never refine; a turn-ending event stops the label.

Rule: a `[skip ci]` commit still needs the local browser tests for the files it
touches. A helper moved out of a closure must be checked for captured locals.
Known pre-existing failures on main in this sandbox (unchanged by this fix): 6
tests in tests/test_ui_playwright.py (not_started x2, model_route, background
refresh, shadow copy send fails, session unavailable).
