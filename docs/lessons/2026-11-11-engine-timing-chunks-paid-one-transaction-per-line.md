# 2026-11-11: engine timing lines each paid a transaction

## Problem
The stderr drain synchronously called the timing sink for each observed stage line. Eight timing lines meant eight
progress-event transactions, plus a session-to-task lookup on a cold mapping. The reap path suppressed a failed stderr
task, so a failed timing write could be reported as successful execution.

## Change
- Collect only complete, recognized timing lines already present in one stderr read chunk. Flush immediately after parsing
  that chunk. No timer, waiting for another stage, or end-of-answer buffer.
- One scoped statement inserts the ordered progress events, resolves each session's visible task, touches each matching
  task once, then sends after-commit wakeups. Unknown/deleted/foreign-owner tasks receive no events.
- A sink failure records a diagnostic, wakes the active operation's failure wait, and raises after cleanup. The active slot
  is released. A parked child's failure is recorded immediately and is raised on its next operation or reap.
- Legacy per-line sinks remain supported. Production interview clients use the chunk sink.

## Measured and limits
Eight events already in one chunk cost one transaction / three syncs (identity, statement, commit), instead of at least
8 transactions / 24 syncs before any cold mapping read. A single-line chunk still flushes immediately and costs three
syncs. The gain depends on actual chunking; this is not a guarantee that all eight stages in a live answer become one
transaction. Separated live stages must remain visible. The sink still runs synchronously; this does not claim to remove
all event-loop stalls from database calls.

## Tests
Fail-first database count and sink-failure tests. Eight lines written in one chunk must be flushed while the child is still
running (the child waits for the sink's acknowledgement). Ordered rows, owner/deletion isolation, all-or-none transaction
rollback, failed batch cleanup, existing supervisor lifecycle/stop and timing tests run.

## Rule
Batch evidence already available, not evidence that will arrive later. Performance work must not hide live progress or
turn a failed durable write into a successful run.
