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

Entry claim now shares the short entry transaction and immutable capability.
Input reload is gone: the parent verifies the retained content-addressed input
in memory; recovery still reads immutable DB records. Completion evidence,
completion event and fenced worker status share one short finish transaction.
The final status write locks live task/session rows and checks original
owner/auth/generation/Stop. No DB transaction spans provider execution.

Later whole-answer local proof: cold success 58 (parent 29, child 29), genuine
warm follow-up 39 (parent 27, child 12). Three turns settle exactly six money
touches and the summed actual balance. This is still NOT the <30 target.
Do not mistake the child-only warm count for the whole answer.

Final success counts after the cold-claim and result-channel changes: 29 cold,
29 next cold, 26 warm. Missing usage 28; provider refusal 32; Stop 50-51 in the
observed runs. Only the measured success and missing-usage cases are under 30.
The trace counts the entire submit path, including both money touches.

The private result channel chunks the actual post-publication snapshot and
accepted event, including the real phase. Frames require the operation's
private nonce, ordering, size limit and digest. Missing/malformed/oversized
results use the durable fallback. Final completion SQL also checks the DB's
answer operation and published sequence, so a memory-only result cannot mark
work complete. Reuse the binding's control instead of re-reading the same row.

The watcher reuses the verified entry and captures its event version BEFORE
entry validation, so an intervening Stop wake is not lost. Later revocation/
cross-process checks keep the existing safety cadence and final fences.

The exact `req rebuild --apply` harness runs the real launcher, dispatcher and
orchestrator against local git origins, with effect doubles for image, migration
and preview work. It proves routing and owner gates, not real Docker acceptance
or live deployment. No Docker executable is available in this environment.
