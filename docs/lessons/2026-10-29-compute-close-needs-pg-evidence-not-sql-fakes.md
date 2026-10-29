# Compute close needs PG evidence, not retired SQL fakes

2026-10-29: seven close/release tests failed because their canned SQLite-style
store lacked the native scoped SQL API. Replace it with disposable restricted PG,
real owner/session/task/jobs, production begin/close/release operations and
explicit test evidence rows for failed/terminal/in-flight states.

Preserve close/held/idempotence/preview/selective-release/audit/no-charge rules.
Replace fake write-list assertions with unchanged actual contribution count/sum,
account balance/remainder and audit/state snapshots. Audit order is compared by
actual IDs, not reverse chronological display order. Add a real prepaid account
so no-debit checks cannot pass vacuously on a missing account.

Orphan compute segments cannot exist under the real FK. Replace that canned-row
endpoint assertion with proof that PG rejects an orphan insert plus the pure
release_decision(None) defensive rule. This loses endpoint behavior on an invalid
FK shape only, explicitly not a reachable PG state. No constraints disabled.
All14 tests retained; no production/default-bootstrap change.

Fail-first7failed/7passed0.20s. Compute-close + existing PG close + native retail:
17passed current1.54s,17passed held PG-first1.96s onPython3.11.17.
HostedCI/full suite not run. No deployment, external DB, provider calls or spend;
account and evidence rows exist only on the throwaway test server.
