# Stop parsing unchanged usage and lifecycle history

Usage fallback formerly read and parsed both JSONL documents on every request.
Lifecycle state and anchor checks repeatedly parsed their entire event document,
including an extra full read merely to test existence.

Use a private RLS projection keyed by session/document, with the chunk epoch and
ordinal as its durable cursor. Verify every newly consumed chunk hash and exact
ordinal sequence. An overwrite/clear changes the epoch and resets derived state.
Serialize updates under the existing task transaction. A restart can resume the
projection without a process-local cache or filesystem fallback.

Usage retains unrounded totals and stage/provider/model buckets, plus rejected
and invalid counts. Evaluate an unterminated last line only for the current
response; never commit it twice when a later append completes it. Original
cost_rollup's floating-point addition order is interview then spec. Therefore
changing the interview prefix forces a one-time spec replay, rather than silently
changing a last-decimal rounding result. Normal spec appends and unchanged reads
consume no old lines. Explicit row-level provenance still intentionally reads rows.

Lifecycle needs the latest two matching events, not merely state: a RESUME retry
must prove its immediately preceding FINALIZE content and anchor. Retain that
pair durably. Do not weaken the shared transition table or original Stop fence.

Tests first exposed the absent summary, then rounding-order drift when two area
subtotals were added. Exact row-based comparisons caught the drift; the prefix
signature fixes its cause without loosening the comparison. A rollback test must
use the owning task transaction, not recursively enter the same non-reentrant
checkpoint cursor lock.

This schema and the preceding chunk schema need owner review/application. No
live database, data deletion, deployment or paid provider run is part of this PR.
