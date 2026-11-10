# Remember engine session -> task per tenant, drop it on every write that can change it

Date: 2026-11-10

Problem: the child supervisor's timing sink looked up the task for an engine session up to 13 times per answer, each its own
transaction (~5 round trips on a ~164 ms link), for a mapping that only changes when engine_session_id is set/rebound or the
task is deleted.

Fix (owner confirmed single delivery-api process per task, 2026-11-10): `session_task_cache` keyed (store, tenant, session).
A hit skips the transaction. Dropped by any store write whose SQL names engine_session_id, deleted_at or deletes tasks, and
again after the transaction ends (commit or rollback). Nothing is stored from a transaction that wrote the mapping, and a read
that overlapped an invalidation stores nothing (version read before the query). Misses/None always read the database.

Deliberately NOT cached: engine_session() (capture, stop and fence callers), session_binding, task generation/stop, auth
liveness. The wider task_read_cache is unchanged.
Limit: a write that bypasses the store (manual SQL, another process) is invisible: that is the single-server assumption.
