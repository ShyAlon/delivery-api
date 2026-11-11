# One correction must also publish actual-only display evidence

The inactive runtime test first failed because finish corrected the account but wrote no retail phase display. Final token and compute contributions now enter the existing finish transaction. Their inserts update existing task/phase projections through the existing trigger; they never call the legacy contribution function, which would debit again. Start writes no display contribution, so customers see actual A, not E+A.

Allocation uses the account's existing fractional carry in deterministic manifest order, then call-weighted compute phases. A long-fraction test exposed PostgreSQL numeric division rounding in per-phase /10. Multiplication by exact decimal 0.1 preserves the allocation sum and carry. A guard rejects any mismatch atomically.

Final evidence keys include owner, original job, answer and attempt/phase as a structured array, avoiding cross-owner or ambiguous-concatenation collisions. The rollback test occupies a display key intentionally and proves the correction and usage inserts do not partially commit.

All outcomes log start/final estimate, actual/correction, unknown count, compute and timestamps, not only overruns. Unknown remains pending with E retained, not a fabricated zero. These log fields contain no input/ref/digest/request/response payload. A replay of finish on the same run emits no second final record.

This is local unactivated work. It is not host wiring, exactly-once crash recovery, production pooler verification, full-answer <30 Sync proof, or owner approval to apply the migration. PR747 must not land alone. PR748 is the independent no-migration bundle.

## Host integration checkpoint

Explicit internal injection into ChildInterviewClient now passes real paid-route graph tests for success, unknown usage, HTTP refusal and Stop. Successful replay uses the original receipt/job and requires a later published sequence plus completion evidence. Changed input is refused. Pending outcomes are not redispatched. Recovery of a committed settled answer only completes the original job. No production constructor selects this mode.

The first parent-only measured successful answer was95-98 Syncs (timing chunk arrivals vary),30-31transactions, before deduplicating one input-ref read. Child persistence is additional and not counted by that number. The estimated10-17total budget was wrong for this initial wiring. Payment-only two Sync proof is not a whole-answer proof. Remaining repeats include input/operation retention, job/claim/capability validation, multiple timing chunks and completion/publication. Do not publish this branch as a finished candidate.

Parent relayed the user's university constraint and request for a complete locally proven candidate, one migration/apply+rebuild command and one-file debug trace including parent+child per-answer phases/callers, E/A/delta/unknowns. These remain unfinished, not user steps to try now.
