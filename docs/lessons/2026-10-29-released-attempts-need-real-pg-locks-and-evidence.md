# Released attempts need real PG locks and evidence

2026-10-29: unchanged main produced29failed/8passed0.59s because a canned
SQLite-style store no longer implements the native scoped SQL API. Do not add
new fake SQL responders or disable owner locks to make native ledger tests pass.

Retain37cases, replace29ledger cases with disposable restricted PG owner,
session, task, claimed jobs, attempts, pending credit charges and a nonempty
prepaid account. Remove the autouse owner-lock bypass. Exercise the real store
transition wrapper so lock acquisition and writes share its scoped transaction.

Assertion changes: replace SQL-string/write-list checks with persisted released
attempt/charge states, NULL charge/usage/settled evidence, unchanged actual
balance/remainder/contributions, and exact actor/task/action/proof audit. All
16invalid-proof cases,5wrong states,5usage fields and replay compare complete
persisted attempts/charges/accounts/contributions/audits before/after refusal.
Release limits use actual recent released rows; prove acceptance just below and
refusal at each per-job/per-owner cap. The old fake's contradictory per-job vs
per-owner counts are not representable, so test the two boundaries sequentially
with separate jobs. No behavior assertion or case removed. SQL placeholder/CAS
text inspection is replaced by state/effect proof, not a concurrency proof.
Gateway/socket/migration/source-state tests untouched.

Expanded release + release-audit + native-retail slice Python3.11.17:
41passed current3.19s,41passed heldPG-first3.36s;1platformdirs warning each.
HostedCI/full suite unrun. No production/default-bootstrap change, deploy,
live DB, provider calls or spend. Seed rows belong only to throwaway test PG.
