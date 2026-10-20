# Lessons

Short rules that came from real failures. Add to this file when something goes wrong in a way a rule would have prevented.

## Diagnostics and recovery live in the app

- Put diagnostics and recovery actions in the Debug window: session-scoped (they read and act as the signed-in owner) and audited. Do not build outside-in ssh or database scripts for them.
- Reason: the database enforces row security per owner and needs a live session record. An ssh script connects as the bare login and sees nothing.
- A check that cannot see data must say BLIND and fail. It must never print "0 rows" or "clean". Print which database, login and owner scope it used, and how many tables it could see.

## Accounting

- A failed job that never reached a provider must not leave accounting that blocks the owner's next call. Decide from evidence (provider calls, token charges), not from the fact that it failed.
- Every release or close of an accounting record writes an audit entry and never posts a charge.
- Accounting rows reference jobs. Deleting a task with accounting rows hides it and keeps the rows.

## Tests

- Postgres tests are skipped unless DELIVERY_TEST_POSTGRES_DSN is set, and nothing in CI or scripts/test-local.sh sets it. Until a harness exists, a change verified only by those tests is not verified. Say so in the PR report and add logic tests that run without Postgres.
- Fix causes in code, not in tests. When a real consequence changes an existing test, update that test and do not weaken it.
- Tests stay hermetic: no network, no real clock, no shared state.

## Scripts and templates

- Scripts take no required arguments, switch to main themselves, and abort without writing anything. Every abort prints "Fix:" and "Then run: req".
- A script that needs a Python environment builds its own `.venv` when it is missing.
- A stale or leftover checkout is a template defect, not the user's mistake: the script recovers from a leftover branch and pulls main with fast-forward only.
- Scripts target macOS bash 3.2 and portable tools.
- Dry-run the whole script in a throwaway-origin harness, including the decline path, before sending it.
- Never assume a stand-in clone is main. Fetch every edited file from main and build edits as exact-text pairs with count guards.
- Secrets never go through chat. Spend needs a typed GO with a ceiling.

## Capacity

- Generator phases run one child process each (`DELIVERY_RUN_ISOLATION=process`, the default). `DELIVERY_RUN_ISOLATION=thread` runs one phase at a time and is the rollback.
- The server container is limited to 3 GB and the service and every child share it. The capacity view reads that limit, not the machine's memory.
- `DELIVERY_MAX_CONCURRENT_RUNS=2` is a starting value, not a measurement. Revisit it once the peak memory per run (logged as `run.finished` with `peak_rss_mb`) is known on the real server.
- The operator Debug section "Server capacity" shows slots, running phases, machine memory and the biggest run seen. Set the slot count from that, not from a guess.
- `DELIVERY_PHASE_TIME_LIMIT_S=1800` stops a run that passes 30 minutes. Revisit with real run times.
- Each run is sampled every 10 seconds (CPU, memory, peak) and the container is sampled with it. Samples are in `run_metrics.db` in the data directory, so they survive a restart. Raw rows are kept 3 days, then each run becomes one permanent summary row (average and maximum). Debug > Server capacity shows the recent runs. Size the slot count from these numbers.

## Credit ledger

- Every change to a credit balance is recorded by the database itself (`credit_ledger`, filled by a trigger on `credit_accounts`), in the same transaction as the change. Code cannot move credits without a row.
- Code names the kind, actor, reason and reference with `credit_audit.attribute(...)` before the UPDATE. A change that does not is recorded as `unattributed` and shows up as such in Debug. Fix the writer, do not hide the row.
- No history was backfilled. The first recorded change starts the history.
- Apply `supabase/migrations/20261020080000_credit_ledger.sql` in the Supabase SQL editor, then run `docs/credit-ledger-check.sql`. Code deployed before the SQL keeps working: history reads say the ledger is not set up yet.
- Limit: a database owner can still drop the triggers. The ledger covers everything done through the app and the operator scripts.

## Cost tooltip

- The session-cost tooltip lists credits per phase (`/api/tasks/<id>/cost-phases`, from the same debit rows as the total). Phase names and credits only: no vendor cost, rate or spend limit. Sessions without a task show the plain tooltip.

## Operator and app pages

- Every table keeps its column headers when it has no rows. The "nothing yet" note goes in the table body, never in place of the table.
- All pages share one header (logo, navigation, avatar menu) and one footer. The operator pages are navigation items for administrators, not a separate tab row. Operator pages use the app's styling.
- A browser that opens a page without a valid session is sent to the sign-in page. Only API calls and scripts get the JSON 401. After sign-in only `/ops` and `/credits` come back (a fixed list, never a link taken from the address).
- The container's CPU and memory are read every 30 seconds whether or not a run is active (`DELIVERY_METRICS_IDLE_INTERVAL_S`). Reading them only while a run is active left the operator page saying "Not Available" on an idle server.
- Logs download as a zip holding one text file. The text itself is unchanged and still scrubbed.
- The signed-in person is shown by email (verified token claim). The fixed label "Signed-In User" is only the fallback.
