# 2026-10-26 - The release path must cover the case that created it

Incident: a job that crashed after its provider calls settled left a compute
segment `unresolved` forever. Admission denied every new Start on that
account, and the owner's own release button could not help: `releasable`
required zero provider calls, which is exactly not this case. The recovery
tool and the incident disagreed about which rows were recoverable.

Lessons:
- A release/repair affordance has to be designed from the failure shapes
  that produce stuck rows, not from the happy path. "Never reached a
  provider" was the only releasable shape because it was the only one
  considered.
- "All provider calls terminal" is a money fact, not a UX detail: settled
  calls mean the charge is final, so waiving the stuck deterministic
  compute charges nothing twice. A call in flight (reserved / dispatched /
  uncertain / held) is the opposite fact and must keep holding.
- One rule, one home: the eligibility predicate lives in compute_segments
  and is shared by the owner release path, the ledger view and the operator
  reconcile script. Two rules drift; the drift is always discovered by the
  user whose account is stuck.
