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
- The header exists once (`web/shell-header.html`) and the server puts it into every page. A page never carries its own copy: two copies drifted (the session-cost slot was missing on the operator pages, so the navigation sat in a different place).
- Page-specific styles stay inside the page's own content area (`#frame`). A rule on a bare `button` for one page also hits the header's avatar and breaks it.

- The preview (`ops preview`) runs the merged-main image on a Mac against the real database with execution forced off and no model key. Its buttons that write are real writes. It copies settings to a mode-600 temp file and removes container and files on exit; Docker keeps the runtime env while the container exists, so there is no claim of secure erasure.

- The server image is built under a fixed umask 022 (a private umask made source files unreadable to the non-root image user). The final tag `requi-full:TAG` is created only after `scripts/docker/smoke-image.sh` passes on a candidate tag, and build, preview and deploy-full all use that one smoke before reusing an existing tag. A failed one is removed by its exact tag only.
- `ops preview` makes a fresh engine token per run for the container's two internal processes (never the live one) and runs the image's own `full_entry.check_env` on the exact settings file before starting, so a missing setting is named at once. Settings are an allowlist (DELIVERY_* only); anything the image needs beyond that is added by name, never by widening the filter.
- The preview sets its own sign-in origin, `http://127.0.0.1:PORT`, and drops the live one. Sign-in rejects any other browser address (the live site's, localhost, a different port) by design; the guard itself is unchanged. Open the preview exactly as printed. A rejected sign-in now says the page was opened from a different address, not to look at the task audit.
- The preview accepts sign-in from exactly two browser addresses, `http://127.0.0.1:PORT` and `http://localhost:PORT` (DELIVERY_ALT_ORIGINS, honoured only when DELIVERY_PREVIEW=1; the live service ignores it). Anything else is still rejected. The server names the four commits of the running image (REQUI_BUILD_REPOS) in the startup log, in /health's repositories and in the browser console, so a version is what is running, never what is newest on GitHub.
- A task whose engine session is not found in this environment (control snapshot failed, no live process, engine 404 code 120) shows "Needs attention" and says only what that snapshot shows: unavailable in this environment, no active process reported, the last recorded time. It never says the work is lost, that nothing runs anywhere, or that a rerun is needed. `PREVIEW_CHECK_SESSION=<id> ops preview` adds a read-only yes/no check of the engine state file on the server and in the copy (no contents, no counts); absence at one path is not a statement about any other place. The Debug "Copy everything" fallback downloads a ZIP built in the browser. The header has no All sessions link.
- Cleanup debt after removing the session pages: `web/style.css` still holds rules only those pages used (`.trace-canvas`, `.trace-tooltip`, `.trace-node`, `.trace-edge`, `.inspect-item`, `.bundle-cards`, `.bundle-workspace`, `.bundle-section`, `.session-card`, `.session-v`, `.session-controls`, `.home-sessions`, around lines 11-25 and 117 and 301). They share lines with live rules, so remove them with a before/after screenshot check of Home, a task page, a gate and the Debug dialog, not blind. `.json-view` and `.page-head` stay: other pages use them.

## Process isolation and test harnesses (2026-10-24)

- A check that reads a file must name the file. A bare `grep -q PATTERN` with no file argument inside a script reads the script's own stdin, so preview-exec's ORIGIN checks silently consumed stdin and never checked the built env file. Fixed by passing the file argument (e218a4d); regression coverage proves both directions (test_confirmed_start_passes_origin_checks fails on the unfixed script, passes fixed).
- `InProcessEngine.dispatch` (DELIVERY_RUN_ISOLATION=thread) can mask a failure as a permanent hang: if the phase lifecycle raises before the runner feeds the virtual process its command, the worker thread blocks forever in `proc.wait_command()` and the real error never surfaces. Observed as an admitted reference interview stalling silently - no events, no in-flight engine call in an all-thread dump. Diagnose a suspected wedge with PYTHONFAULTHANDLER=1 and SIGABRT (the dump shows whether anything is actually blocked) before treating it as a deadlock.
- DELIVERY_RUN_ISOLATION is read only by tenant_jobs dispatch, run_slots status and the app stop/stoppable paths. The interview path never reads it. Do not attribute interview behavior to the isolation setting without a named reader on that path; an empirical correlation without a mechanism is a lead, not a cause.

## Connection replacement must end in a value or a raise (2026-10-26)

- A retry loop that must produce a value ends in an explicit raise, never in falling off the end: `_begin` tolerated one dead reused connection, and when the replacement idle was also dead the loop ended and the method returned `None` implicitly, so the failure surfaced as `TypeError: cannot unpack non-iterable NoneType object` at the caller's unpack, far from the cause. After one dead reused connection the replacement is now one direct fresh connect that skips the idle pool; a capped pool falls back to one normal checkout; and the exhausted path raises a bounded `OperationalError` naming what happened.
- A burst of server-killed idle connections is a normal event (idle reaping, pooler restarts), not an edge case: reproduce it in tests by really killing backends (`pg_terminate_backend`) on a disposable Postgres, because mocked close paths do not exercise the server's half of the failure. The regression test kills two idle backends in a row and asserts the exact pre-fix `TypeError` at the same file:line.
- Retrying a transaction start is safe only because nothing business-visible ran yet: the only statements attempted on a dying candidate are the transaction settings and the identity claim, so a replacement can never replay a caller's read or write. Keep it that way: replacement belongs before the first business statement, never after.

## 2026-10-26 - `git add -A` sweeps run artifacts into commits

Twice in three days, local run artifacts landed on main: pytest chunk lists (`c1`, `c2`) and four 163KB test databases named after relative PG connection strings under `host=/tmp/...`. Cause in both cases: a local test or script wrote files into the workdir and a later `git add -A` committed them unseen. Prevention: review untracked files before staging (`git status` before `git add -A`), keep run outputs in /tmp, and keep the `/host=/` gitignore guard so the relative-DSN accident cannot re-land.

## 2026-10-26 - a new log source must reach the per-task export, in its own tag format

The engine process log (engine.log) was added to the everything ZIP but never
to the per-task Debug ZIP, and the per-task filter matched only the delivery
activity line format (quoted ids); engine dotted-event lines use unquoted
session=/task= tags and would have been filtered out even if the file had
been included. Prevention: when a log source is added, wire it into every
export that filters logs and test the filter against the source's own line
format, not the format of the sources already there.

- [2026-10-31: incremental admission totals](lessons/2026-10-31-admission-running-totals.md)
