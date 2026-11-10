# 2026-11-10: the answer handler did database reads on the event loop

## What happened
`loop.stall.stack` in the 11-10 ZIPs showed the event loop blocked 416-482 ms at a time inside one database round trip,
at app.py:1346 (`store.task`), 1427 (`store.add_event`) and the transaction begin. `loop.lag` after each answer was 1.8-2.6 s,
and unrelated requests waited (GET /api/model-routes took 5.8 s for 9 queries).

## Cause
`answer_interview` is `async def` but called `store.*` and `runner.*` directly. A synchronous call on the loop thread blocks
every other request for the whole round trip (~160 ms to the database, more with the transaction envelope). Only the
engine submit was off-loop.

## Fix
Every store/runner/get_task call in the handler goes through `run_in_threadpool`. An AST test fails if a direct call comes back.

## Rule
An `async def` handler never calls the database directly.
