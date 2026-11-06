# DB-cost replay evidence - 2026-11-06

All traffic used disposable local Postgres and a strict fake HTTP provider. No real providers, paid execution, remote DB writes or deployments.

## Comparable results

Same current interview-engine/spec-generator sources held constant. Request counts are the app RequestStats db_queries, not all SQL wire statements. SET ROLE, some scope setup and child-process statements are not all counted, so these numbers are a lower bound on database work. Milliseconds from local Postgres do not predict remote database latency.

| Delivery commit | Provider delay | POST status | POST queries | POST duration ms |
|---|---:|---:|---:|---:|
| 4020814 | 3s | 200 | 190 | 4792 |
| c1cf148 | 3s | 200 | 206 | 4780 |
| f660f3c | 3s | 200 | 206 | 4845 |
| ff0646c | 3s | 200 | 206 | 4802 |
| eb296b5 | 3s | 200 | 206 | 4980 |
| 4020814 | 0s | 200 | 166 | 1726 |
| ff0646c | 0s | 200 | 173 | 1839 |
| 4020814 | 15s | 200 | 286 | 16860 |
| ff0646c | 15s | 200 | 338 | 16938 |

## Findings

- Comparable Nov 2 candidates c1cf148, f660f3c, #636 ff0646c and #638 eb296b5 all return 200 with 206 counted queries at a 3s fake answer delay. The Nov 6 baseline 4020814 returns 200 with 190 queries. These candidates do not establish a new query-count regression.
- Increasing fake answer latency from 0s to 15s adds 120 counted queries on Nov 6 main (166 -> 286), or 8 counted queries per second. On #636 it adds 165 (173 -> 338), or 11 per second. Long-held POST counts include background watcher activity, not only work required to accept the answer.
- Measurement-only ablation disables the app progress watcher, not any production code. At 15s delay main falls from 286 to 232 queries. The remaining child stop/auth/generation watcher still runs every second. Disabling it is not recommended: it enforces stop and revocation.
- Top recorded shapes include scope/auth setup, task/session reads and progress cursor reads. Raw wire instrumentation double-counted Connection.execute plus Cursor.execute and was discarded; final histogram uses RequestStats.statements.
- Hot GETs are stable across comparable candidates: first task GET 2 queries; warm cached task GET 0; phase-output 16; credits 5; post-answer task GET 9. These are fixture-specific counts, not production cardinality estimates.

## Limits

10b0020 (Oct 31) and child-cutover merge 9fce940 cannot complete start with today's interview-engine: child descriptor closes before terminal evidence. They are incompatible runs, not zero-cost measurements. A historically matched engine/spec-generator matrix is still needed to prove or reject the initial child-cutover regression against Oct 27. Current evidence identifies ongoing 1Hz watcher cost, but does not establish its introducing commit. No DB-cost fix PR was opened from this incomplete historical attribution.

The app watcher and child watcher are sibling instances of the same polling-cost class. A fix must preserve visible progress plus stop/revocation responsiveness and be evaluated against latency/query budgets, not replace one-second checks with an unapproved long blind window.

## Reproduction

`DB_COST_PROVIDER_DELAY=15 scripts/bench/db-cost-bisect.sh 4020814 ff0646c`

`DB_COST_PROVIDER_DELAY=15 DB_COST_NO_PROGRESS_WATCH=1 scripts/bench/db-cost-bisect.sh 4020814`

The script retains full per-candidate logs and returns candidate exit status in BISECT_EXIT. Historical runtime compatibility is not silently patched.
