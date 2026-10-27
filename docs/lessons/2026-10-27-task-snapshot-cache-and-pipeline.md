# 2026-10-27: merge task reads before adding sockets

The task presentation read repeated the task row, engine binding, timeline and
artifact reads. One SQL statement now returns the task (phases/gates included),
newest 500 events, artifacts and, when enabled, the verified manifest. Both SQL
dialects have tests. Missing optional manifest infrastructure is not queried
when its feature flag is off. Hash validation remains fail-closed.

A bounded process-local cache keys by store object, verified owner/session,
task and manifest-read mode. Copies isolate callers. Event-bus versions and a
conservative store-wide commit epoch invalidate entries. PostgreSQL invalidates
before writes and again after transaction completion, including shared request
transactions: otherwise a concurrent old read could repopulate during a write.
WITH statements conservatively invalidate too (they can contain writes).
Internal get_task calls never use the presentation cache: stale data cannot
become an answer/gate mutation precondition. Engine snapshots are still live.

Cross-process changes do not publish on this bus. A strict 60-second TTL bounds
that staleness; multiple processes need shared invalidation before a longer TTL.
The TTL does not extend on cache hits. Cache invalidation is deliberately broad:
an unrelated store write can evict another task. That costs extra reads, not
incorrect presentation.

Psycopg 3.3.6 supports pipeline mode. Availability does not imply one RTT for the
whole request: session checks and result fetches are synchronization boundaries.
This change batches BEGIN and claims setup, then exits pipeline mode before
private reads. Connection.transaction() entered inside pipeline() leaves nested
pipeline contexts active until commit and can defer errors into the NEXT write.
Use public psycopg.Transaction directly, synchronize, and prove duplicate-write
errors still surface on the right statement. Fake connections must implement
both Transaction and pipeline contracts. TimingConnection owns statement labels
once; the SQL bridge owns query counts, avoiding duplicate timing labels.
The setup label carries synchronized batch wall time (including BEGIN), not
misleading enqueue latency or a guessed per-statement server duration.

Measured hermetic harness (base 8a51746, same scenario): local GET task cold
7 SELECTs -> 1; repeat 7 -> 0. Real isolated Postgres: setup + snapshot = 2,
warm = 0 without connection checkout. A middleware session-check miss adds its
own SELECT, and live engine/decision operations may add work. These are counts,
not a promise of remote wall latency. No live DB, deployment, restart or paid
provider calls were used.

SQLite diagnostics also called a cloud-only worker method. Guard it by runtime
mode; route tests now run without stubbing the missing method. The browser ZIP
check rightly changes from 6/8 to 7/8 sections collected.
