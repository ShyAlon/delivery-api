# Keep answer observation separate from durable journal progress

## Problem and evidence

The owner ZIP captured on 11 November at 20:38 IST measured four answer requests at 75-83 request syncs, with 42 cold / 39 warm inner syncs. Warm requests repeatedly logged `interview progress cursor gap`, including their final drain. The answer-only memory stream uses microsecond-scale cursors. The delivery watcher incorrectly treated those cursors as the durable engine journal's consecutive integer stream. Failures cleared its cursor cache and repeated database work without retaining the intended stage progress.

A host-only fixture with a lambda money gateway did not expose either the outer watcher defect or real durable model-pin lookup costs.

## Fix

- Give the watcher explicit answer-observation and durable-journal read seams. Its two cursors never share a value or storage.
- Retain answer stage/item history with existing event-ID deduplication, tenant RLS and a task lock. Do not advance the durable source cursor from transient observations. Heartbeats and successful per-call summaries remain out of copied task history.
- Read the durable cursor inside the set-based page-write statement, after acquiring the task lock in a separate statement. Combining the lock and cursor read would permit a stale snapshot after lock contention. Invalid pages produce no writes.
- Carry the same cursor cache into final drain and across pages in one drain.
- Skip global model-default discovery when the model is already present in an immutable reviewed task factory. Unknown models still require the existing reviewed-route check.
- Run fresh single-statement task-pin and worker-job reads in the existing read-only one-trip transaction batch, not a new cache.
- Batch the two submit-stage events and the two record-stage events without changing their order, contents or durable anchor. Offload recovery/final-reconcile database work from the async handler.

No new migration, feature flag, paid replay, operator step, spending limit or retry cap. Receipt start/finish, fresh final authority, Stop and publication fences remain unchanged.

## Local proof and limits

The new signed-HTTP test uses real DurableGateways, ProductionGateways, restricted disposable PostgreSQL, actual child processes and a localhost fake provider. Only vendor website verification, balance transport and provider HTTP destination are substituted. Fixture credit is not real money.

On untouched main, that three-turn graph measured inner 42/39/39 (parent30) and request 77/74/78 syncs. On the revised implementation it measured inner32/29/29 (parent20), request57/58/58 in the final sample. Request counts vary with event coalescing; regression budgets allow that variance. All three receipts settle; next inputs publish; the revised run logs no progress ingestion/final-drain failure. Transient cursors never enter progress_ingest.

The existing simplified host graph remains cold29/warm26 because it never had these production gateway costs. Do not compare its boundary with full HTTP counts. Stop remains41 in that fixture and is separate debt. Owner latency savings are an estimate until the next real-infrastructure trace; localhost milliseconds are not a remote benchmark.

Broad local gate: 2,428 passed, four existing skips across the previous 287-file fast selection, one unchanged customer-surface vocabulary failure. Node-only render fixtures need a browser-history global preload; no product behavior or assertion was changed. Focused real-child, HTTP, tenant, money, progress, rollback and Stop tests run separately without paid calls. Hosted CI remains billing-blocked; no billing settings changed. Docker, macOS and the owner's remote pooler were not run here.
