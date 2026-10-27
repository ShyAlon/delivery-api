# Waiting-input evidence needs atomic dedupe

A task GET already owns an event snapshot when it reconciles the engine's open
input. Two requests can both see no waiting_input row, then both append it.
Sequential GET tests cannot prove the once-per-input contract.

Keep the snapshot check as a cheap read fast path, but make the write decision
atomic in the store. Postgres locks the existing owner-visible task row before
reading committed events and inserting; independent adapters/processes share
that lock. Never add a Python mutex across a PG wait: request transactions may
hold the row lock until their response ends and that creates AB-BA deadlocks.
SQLite begins an immediate write transaction, including across two connections.

Deduplicate exact task/input identity. Keep the first event's id, timestamp,
phase and prompt immutable. A duplicate does not update task time or notify the
wake bus. Other inputs still append. No schema migration or broad grants are
needed. Historical duplicates are not rewritten by this change.

Two stale-snapshot regressions failed first on main (SQLite and isolated real
Postgres). The new tests also cover request-scoped PG transactions, owner
isolation, immutable replay, new input and invalid-input rollback. The existing
mutex/row-lock deadlock regression stays unchanged, including its two generic
add_event rows: add_event itself is intentionally not globally deduplicated.

Validation: 122 tests across waiting-input dedupe, HTTP cutover and the complete
Postgres store file pass, no skips. Hosted CI and full-repo suites unverified.
No live DB access or deployment performed. Idle checkpoint-reader diagnosis
(rank 14b) is separate.
