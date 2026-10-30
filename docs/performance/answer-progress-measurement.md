# Measure answer/progress changes

No new server setting, manual approval gate, paid test call or DB migration is needed for this change. Merge both progress PRs, pull both repos, then use the usual owner-managed build/restart when existing work is quiescent. No restart during paid work.

## Existing baseline, not an estimate

Owner preview log, 2026-10-30 19:46:12 UTC, answer reference d29a8db4:
- Answer duration_ms=142588, db_queries=576, db_ms=124099.
- engine_calls=23, engine_ms=174622. These are engine operations, not provider calls.
- Progress page: events=5, fetch_ms=2750, ingest_ms=10349.
- Largest nearby logged loop.lag lag_ms=10810.

DB and engine totals overlap/nest. Do not subtract them from wall time or turn DB time into an exclusive percentage. The original log does not contain a statement histogram sufficient to attribute all576queries to progress.

## Live comparison, using ordinary work

1. Save the preview terminal output around an ordinary answer into before.txt before updating. Keep the http.request line whose path ends /answer and its200status, plus progress.ingest.timing and loop.lag lines. The historical baseline above can also be used, with weaker comparability.
2. After the normal update, save the same window for another ordinary answer as after.txt. Do not submit an extra paid answer just to benchmark. Keep model route, interview stage and approximate answer length similar. Compare several ordinary turns if possible; different provider delays/coverage work make one answer an imperfect comparison.
3. Run locally: .venv/bin/python scripts/diagnostics/compare-answer-progress.py before.txt after.txt
4. Read each answer's duration_ms, db_queries and db_ms, progress events/ingest_ms/fetch_ms, and maximum logged loop lag within the file. Script fails if no completed answer timing exists or a non200answer is present. It never launches a request or reads credentials.

Expected direction, not a measured live promise: fewer progress writes, lower ingestion time and no ingestion-induced event-loop pause. Answer wall time may still be dominated by provider work, checkpointing and other SQL. No loop.lag line is missing evidence, not proof of zero lag. Use short matched windows so unrelated stalls are not attributed to an answer.

## Repeatable local check, no providers

.venv/bin/pytest -q -s tests/test_progress_page_budget.py

It uses owned disposable PostgreSQL, restricted app-role transactions and10ms injected delay per store data-SQL execution. Same five-signal input, original retained path vs new page path:47recorded SQL/6transactions to4/1; observed443ms to36ms in the initial run. This is a page benchmark, not a142s answer benchmark. The delay excludes setup/commit round trips and is not a simulation of all Supabase behavior.

The paired engine change prevents heartbeat and finished-model-call summaries from entering the source journal. Durable stage/item boundaries/failures remain. Delivery filters older-source duplicates, advances every source cursor and stores only durable boundaries. Usage/accounting, checkpoints, Stop fencing, lease renewal and model diagnostics are unchanged.

## Coordinated source + delivery result

Real restricted-role source lease/journal plus delivery, same five attempted signals,10ms injected per data-SQL execution: source journal rows5to2, source queries15to6, source transactions5to2; delivery queries47to4, delivery transactions6to1; combined observed616ms to103ms. Original delivery issued25write statements; new delivery uses one set-based write statement with2dedupe rows+2event rows+1cursor+1task update and at most1phase update. This removes rows as well as network round trips. If all five signals are necessary boundaries, those history rows remain; phase/task/cursor projection still updates only once per page.

No claim that576queries become10or that142.6s becomes24s. The benchmark isolates progress, not the full answer, and source fencing/claims and commits still have real costs.
