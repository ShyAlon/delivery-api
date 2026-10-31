# Confirm intent without inventing worker exit

The owner should not have to prove a vanished worker exited merely to remove their own task from the normal view. Keep two facts separate: confirmed cleanup intent and verified process/accounting outcome.

Worker Jobs uses a binary confirmation naming the task and warning that accepted calls may still finish and bill. The transaction takes the owner admission lock, persists Stop/generation fencing, tombstones the task and audits the action. No worker, attempt, compute segment or charge is deleted or marked settled/done. Engine notification is best effort after the durable transaction. A missing engine or closed paid-start gate does not refuse cleanup.

The own-test transition ignores retained running rows only for hidden tasks. Unresolved provider/compute accounting and scale checks remain unchanged. The owner can view metadata, state events and usage through existing Back office detail rendering. Worker Jobs reuses the shared row renderer, read/error handling and detail infrastructure; a reusable confirmation/busy/write helper handles the new action. Existing older paging implementations are not silently rewritten in this PR.

Tests disclose the changed contract: the old read-only-only button count is updated to expect View and Stop; Cancel issues no mutation; confirm issues one scoped POST and refreshes; real PG verifies owner isolation, durable Stop+tombstone, retained running state, idempotency and accepted settlement after hide. The active-jobs fixture now uses a unique disposable owner rather than leaking pending work into unrelated transition tests. No assertion about accounting integrity is weakened.
