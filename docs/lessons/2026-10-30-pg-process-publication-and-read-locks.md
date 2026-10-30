# 2026-10-30 - PG process parity needs a complete runtime boundary

The default process runner did not inherit the service's PG document and
checkpoint runtime. A successful interview could leave needs generation looking
for a local intent file on the next host. The process now receives its original
job/attempt/session/generation binding through a bounded private descriptor and
uses PG documents, checkpoints and the execution fence.

Lessons:
- Test the default process route, not only the convenient in-process route.
  Cross-host acceptance must verify exact published bytes and no local business
  copy, through the last validation gate.
- Record produced completion before publication. A failed manifest/phase/receipt
  transaction must leave a publication-only retry, never repeat paid work.
  Recovery verifies the complete hash, current authority and readiness receipt.
- Private proposals are not phase outputs. The shared producer must not publish
  a proposal into the live manifest before review and installation.
- GET observations must not retain task/owner locks while the request calls the
  engine on another connection. Short observation transactions still join an
  explicit outer mutation transaction and roll back with it.
- A next prompt can arrive before the previous answer event. Pair transcript
  turns by input identity, not event adjacency, in both UI and downloaded text.
- A short green test cannot prove a lock race is gone. Keep deterministic
  second-connection lock tests and repeat the real alternating-host workflow.

Changed coverage: the former default-flags-off missing-evidence reproduction now
requires exact PG evidence on the second host. Invalid approval still returns
400 and cannot start SRS. A deliberately stale local replica must not override
current PG bytes; it stays untouched. Those changes replace obsolete file-store
expectations, not tenant, hash, approval or lifecycle checks.

Verification uses disposable databases and deterministic provider stubs. Hosted
CI has not been verified. No live reset, deployment or paid call was performed.

Optional connected tests must be enabled under their own isolation contract.
The item6 receipt/export path still demanded files and SQLite after the public
workflow was green. A separate named throwaway database exposed that failure.
Private pipeline receipts now validate PG documents, revisions read immutable
checkpoint snapshots, and exports include the exact accepted result chain.
Never remove an isolation assertion to make an optional test run in the general
harness, and never close a shared pool as though it were a saver-owned SQLite
connection.

Proposal IDs must match the actual producer contract: 32 lowercase hex digits,
not a dashed UUID. A synthetic dashed-ID test hid the difference until the real
connected revision graph ran. Both original and child jobs now use the same
immutable, original-attempt-fenced PG proposal sink. Malformed IDs, conflicting
payloads and Stop are explicit negative tests; the real graph is rerun too.
