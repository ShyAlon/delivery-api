# Measure real barriers, then remove redundant boundaries

The protocol trace counts simple Query as well as Sync. Queuing BEGIN while the
connection still reports IDLE must not cause an implicit second BEGIN. Temporary
autocommit during explicit setup removes that duplicate; restore mode at checkin.
Context-scoped tracing isolates concurrent answers and includes child persistence.

Owner-reviewed answer changes remain internal opt-in only: diagnostic/usage
buffering, transient progress, no DB writes for timing chunks, one short entry
helper transaction, and reuse of an already validated published snapshot.
Policies still execute inside the helper batch; no transaction spans provider
work. No RLS, money-touch, cold restore or final publication relaxation is implied.

Local success measurements from one real graph/fake provider/disposable PG:
175 baseline; 160 diagnostics; 145 transient progress; 121 timing memory/log;
113 parent snapshot reuse. Full counts include parent and child. Entry batching
removed three helper barriers, initially hidden by timing-chunk variability.
These measurements are not production latency estimates or <30 proof.

Follow-on proofs: minimal recovery records lower success 113 -> 107;
checkpoint memory 107 -> 101; immutable handler authority 101 -> 98;
recent healthy socket reuse 98 -> 97. Pool probes are skipped only in answer
mode, not for idle/broken sockets. Authority scope changes still fail and the
post-handler authority check stays fresh. Trace teardown follows fallback
correction, including exceptional paths. No target claim: 97 remains above 30.
