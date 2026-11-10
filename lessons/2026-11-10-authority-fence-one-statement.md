# Authority fence reads task, binding and session liveness in one statement

Date: 2026-11-10

Problem: `WorkerCapability.validate` ran three statements (task, engine_sessions, requi_session_active), each in its
own operation transaction: pool check, identity statements and commit on a pooled link ~164 ms from the database.
A warm answer ran the fence dozens of times (measured: task() 18x, engine_sessions 17x, requi_session_active 8x).

Fix: `store.authority_snapshot(task_id, session_id)` returns task record, binding and liveness from ONE statement
(one snapshot, so the three facts can no longer disagree). `validate` uses it; stores without it keep the old path.

Fence semantics are unchanged and each is tested (tests/test_authority_read_single_statement.py): stop (generation bump),
deleted task, rebound or deleted session binding, revoked auth session, other tenant scope. The count test failed first (3 != 1).

Test seam: tests that faked expiry by patching `auth_session_active` now go through `_set_auth_live`, which also forces the
liveness `authority_snapshot` reports. Patching only the old method would silently stop testing the fence.

Not done here: `require_job`/`capture` reads and the stop watcher (the watcher lives in the stop-watch PR) can adopt the same call.
