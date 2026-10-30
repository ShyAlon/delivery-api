# Keep durable evidence off request latency

## Incident
The owner reported anonymous client-log204s at1616ms and8525ms, PoolTimeout
in log/metrics/startup storage, and3469ms loop lag after schema preflight passed.
These are owner observations, not a live DB session audit by this change.

## Reproduced cause
Activity/diagnostic/stage logging executed synchronous PG round trips under
logging handler locks. Async middleware also calls those handlers, so a slow
append can block the event loop and unrelated worker logging. RequestStats does
not instrument operator log transactions, so db_queries=0 is not zero PG work.
Three injected150ms appends took450.77ms on the async caller before the fix,
versus0.094ms for enqueue afterward. A blocked writer contention test verifies
that an async activity caller and loop heartbeat stay responsive.

## Fix and semantics
One bounded1024-record process queue and one writer share the existing pool.
No extra log pool. Timestamp is captured at enqueue. A204 acknowledges bounded
memory admission, not durable PG storage. Abrupt process death can lose pending
evidence. Debug durability reports pending count, capacity and oldest pending
age; overflow/write failure/drain timeout records sticky degradation with no
recursive logging or private error detail. Normal shutdown drains up to5s.
The writer can wait5s for cold pool connection establishment without blocking
requests; startup report can wait10s. Reads retain250ms checkout. Those waits
are separate from server statement/lock timeouts and do not establish that live
pooler saturation has been solved.

The supervisor writes the engine stdout tee, so it is a persistent DB client.
Its pool was previously uncapped relative to the aggregate runtime budget.
It now caps at1, releases startup pool before child launch, and preflight counts
it. Default total9 delivery +4 compatibility +1 supervisor =14 of15. Local child
mode8+4+1=13. Other preview containers/DSNs/external ops are not verified here.
No live writes, migrations, deployment or infrastructure changes were made.

## Smoke contract
Keep the existing2s post-boot settle and successful anonymous telemetry gate.
It is an intentionally strict user-visible smoke, not a8.5s persistence allowance.
Do not extend it to hide latency. Browser success verifies telemetry admission;
operator durability status is the separate persistence signal.

## Validation boundary
Tests explicitly drain before reading persisted evidence, preserving all original
retention, scrub, recursion, no-file-fallback and diagnostic projection assertions.
Disposable PG and Chromium only. Owner Mac/Supabase latency and hosted CI remain
unverified; after owner rebuild, compare client-log latency, event-loop lag,
writer pending age and pool timeout evidence before declaring the incident closed.
