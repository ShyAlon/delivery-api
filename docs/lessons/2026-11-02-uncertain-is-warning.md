# Uncertain accounting is not a server shutdown

Owner decision: one shared admission path for all calls. An uncertain provider call retains its reserved amount, evidence and audit trail but is not itself an owner-wide or server-wide stop. Unknown compute also must not recreate that stop. Reserved and dispatched calls still consume concurrency; uncertain records are historical review work, not active-call slots. Normal known/reserved amount and confirmed-cost checks remain.

Warn in private Back office and audit the first transition atomically. Never automatically replay, settle, cancel, refund, waive or release the uncertain operation. Same-operation safety and accounting evidence stay intact. Do not add a separate local bypass or an unrequested worst-case-bound subsystem.

Residual risk accepted for review at merge: final total may exceed a ceiling by the unknown bill. A reservation estimate is not a guaranteed maximum. Include this line in the PR, do not claim a hard cap.

Test restricted real Postgres admission with an uncertain job, independent paid and zero-cost calls, unchanged old evidence, one audit entry, normal concurrency and amount checks, both confirmed-cost guards and private warning pixels. Migration is owner-run, dry-run/review first; clears only rebuildable admission projection rows and never authoritative ledger records.
