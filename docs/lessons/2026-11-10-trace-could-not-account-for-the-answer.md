# 2026-11-10: the trace could not account for an answer

## What happened
The debug ZIP showed 449 "round trips" for one answer but logged only 175 of them, and nobody could say which were network trips.

## Cause
- Only the 30 most frequent (purpose, shape) rows were logged; 100+ groups of 1-5 entries were cut.
- Entries are not network trips: BEGIN + identity + SAVEPOINT leave as one pipelined batch but counted as three.
- 129 entries were `unattributed` (worker threads have no delivery frame).

## Change (logging only; no query changes)
- `db.purpose_row`: every purpose, shapes collapsed, with entries, real network syncs, transactions and ms.
- `db.answer_budget`: one line per request with totals and a per-phase split (handler, authority, watcher, engine_events, admission, job, progress, model_pins, pool, other).
- `db.statement_trace` gains `network_syncs` and `transactions`; pipelined entries cost 0 syncs.
- `unattributed` entries carry their thread-pool name.

## Rule
Measure the thing you are going to budget. Rows must add up to the request total so a reader can see nothing was cut.
