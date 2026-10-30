# Progress needs a write budget, not one transaction per signal

Owner goal: minimize DB writes, including source journal and delivery copies.
A five-signal page previously needed47recorded delivery SQL executions and6transactions; a late cursor gap could leave earlier signals committed. Failing tests reproduced those defects and event-loop blocking before edits.

Keep durable stage/item boundaries, coverage explanations and failures. Do not journal periodic model elapsed-time heartbeats or successful-call timing summaries at the application emitter. Their model diagnostics/usage evidence remain; lease-renewal heartbeats are separate and unchanged. Delivery filters older-source transient copies but consumes their cursor. Validate the whole ordered page before writes, then lock task, check cursor and atomically insert retained events/IDs and one cursor/projection update. Offload both cursor read and page ingest from the async loop.

A single combined disposable-PG benchmark uses source lease fencing plus delivery restricted roles and10ms artificial SQL latency. It is not a prediction of remote full-answer time. Document live fields and provide a fail-loud local saved-log comparison. Accounting, Stop generation/lease fences and engine control sequencing are not progress optimizations.
