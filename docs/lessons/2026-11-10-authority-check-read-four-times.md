# 2026-11-10: one authority check made four database trips

## What happened
Each answer runs the worker authority check (`WorkerCapability.require_job`) 6 times. The 11-10 debug ZIPs show
6 each of: authority_snapshot, engine_session, session_binding, task.

## Cause
`capture` read task, session id and binding; `validate` immediately re-read task, binding and sign-in state. Each
read was its own transaction. At ~160 ms per round trip to the database that is about 0.5 s per read.

## Fix
`PostgresStore.worker_authority` returns task, session id, binding and sign-in state in one statement, and
`require_job` makes the same checks with the same error messages from that one snapshot. All 6 checks stay (they
are separate points in time: Stop, sign-out, generation change, lease expiry); each costs 1 read instead of 4.
Trace purposes now keep up to 2 more frames under `require_job` so the next ZIP names the 6 call sites.

## Rule
Count the trips, not the statements. A fence reads once per point in time, not once per fact.
