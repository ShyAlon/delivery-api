# 2026-11-10: GET /execution and /diagnostics spent their time on three statements per read

Evidence (debug ZIP, task 7f3b86686274): /execution 8.3-8.7 s with 24 queries; its trace shows 4x
auth_session_active, 4x session_binding, 4x task, all under historical_checkpoint:validate (about 620 ms per
group of 4). /diagnostics 8.3-9.4 s, 17-19 queries, 3x SAVEPOINT under the same validate.

Cause: each document read validates HistoricalCheckpointCapability with three separate statements. WorkerCapability
already reads task, binding and liveness in one statement (authority_snapshot).

Fix: historical validate uses authority_snapshot when the store has it. Authority is still read fresh on every call,
never cached; same denials (revoked, wrong owner, wrong binding); stores without the snapshot keep the three reads.
Effect: 3 statements -> 1 per validation, so about 8 fewer statements and ~16 fewer round trips on /execution.
Not a fix for per-statement overhead (item C) or for the other queries of these routes.
