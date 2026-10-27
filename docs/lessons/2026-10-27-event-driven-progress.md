# 2026-10-27 - Progress is woken by writes, never polled on a timer

Incident: the SSE progress stream ran a synchronous remote database read ON the
event loop every 2s per open stream; at the measured ~650ms pooler round trip
each cycle stalled every in-flight request, and polls of the page paid three
sequential round trips (claims setup, session check, query) before any work.

What changed:
- The stream waits on an in-process wake bus (event_bus.py): every events-table
  write in store.py notifies AFTER its commit, the stream reads on the wake and
  once a minute as a safety net. A per-task version counter closes the
  write-between-read-and-wait race: the reader snapshots the version BEFORE
  reading, so any write that lands in between re-wakes it.
- All stream database reads run in the threadpool; a synchronous remote read
  never runs on the event loop again.
- The session-liveness check is cached per verified session for 30s. Tradeoff
  (approved): sign-out/revocation takes effect up to 30s late.
- The review screen polls once a minute while its stream is open (stream events
  trigger an immediate poll) and returns to 1.5s when the stream dies; the task
  page's 5s background refresh backs off to 60s while its stream is open.
- Every statement carries a label and milliseconds (RequestStats.statements,
  slowest surfaced as top_statement in the request window); the stream's
  max_cycle_ms is actually stored now (it was measured and dropped).

Lessons:
- Cadence is a budget: the once-a-minute rule from the 2026-10-26 lease ruling
  covers any loop that reads a remote database on a timer. If nothing changed,
  the read must not happen; freshness comes from writes waking readers.
- Notify after commit, snapshot the version before the read: the two orderings
  together make "wake then read" race-free without locking.
- The shared request transaction was already lazy, so a fully cached request
  costs zero round trips - check for an existing lazy seam before adding one.
- Single-process assumption: the bus is exact only while one uvicorn process
  writes all events (the local execution preview; the engine is in-process). A
  multi-worker deploy must replace it with Postgres LISTEN/NOTIFY.
