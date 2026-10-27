# 2026-10-27 - Model-call timing in Task diagnostics

The everything ZIP is assembled in the browser (identity.js) from dialog
sections; "Task diagnostics" is the JSON of /api/tasks/<id>/diagnostics, so
anything added to task_diagnostics.build lands in the ZIP with no JS work.
Per-call model timing (llm-calls.jsonl) projects through
task_diagnostics.model_calls(root, cloud=...) with the SAME privacy rule as
/api/tasks/<id>/execution: on cloud, token counts and derived rates stay
server-side, durations stay visible.

Pre-existing gap found (not fixed here, reported): the diagnostics route
calls store.task_worker_state, which SQLiteStore does not have - the route
500s on local preview. The new route tests stub it.
