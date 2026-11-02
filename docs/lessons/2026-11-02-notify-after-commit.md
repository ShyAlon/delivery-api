# Wake streams only after committed state is readable

Seven PG store paths called notify while their transaction was still open. An independent-thread read inside a deterministic notify probe could not see the inserted event. The existing thread test failed intermittently in full CI. This is a real ordering defect, not a reason to relax its timing assertion.

Queue task IDs on the transaction wrapper. Publish once per task only after successful commit; standalone rollback discards them. Shared read-request transactions defer publication to request completion and restore the queue after a failed operation savepoint. Nested store methods reuse the existing transaction. All seven store wake sites use the same mechanism.

Tests cover independent-connection visibility at notification, standalone rollback, shared request commit/deduplication and rollback, and failed-operation isolation. No new sleep or retry disguises the race. Start-failure persistence ordering remains a separate guarantee.47focused transaction, request timing, event-bus and progress-budget tests pass. Broader store/task batch exceeded the execution window without a reported failure; not claimed complete.
