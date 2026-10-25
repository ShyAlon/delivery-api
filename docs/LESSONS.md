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

## Engine start-failure log line absent from a retained container (2026-10-24)

- Incident: interview start failed at 2026-10-24 19:57:34Z (task 0f840cea9a1e) with the 503 detail "engine program failed during start: InsufficientPrivilege". The serving container requi-preview-exec (image requi-full:c52caf46-3d9c144a-0a90cceb-690648ab) started 19:52:16Z with restarts=0 and was still running when inspected at 23:09 IDT. `docker logs requi-preview-exec 2>&1 | grep -A 40 "interview session start failed"` was empty twice on that retained container.
- Verified at the deployed commits: the detail string has exactly one producer (http_service.py:853 at engine 3d9c144a), immediately preceded by log.exception (:852). No handler/propagate suppression anywhere in engine source; importing the real app leaves all loggers handler-less, so Python's lastResort emits ERROR to stderr even under uvicorn's default log config (verified empirically). The launcher (delivery c52caf46) spawns the engine with inherited stdout/stderr. The diagnostic page port 8001 is preview-exec's default, tying the failure to this container.
- NOT established: why the line is absent from the retained container's logs. Every link checkable without the host holds; the remaining discriminators are host-side: whether engine uvicorn startup lines are present in `docker logs` (proves capture at startup only, not later emission), the daemon's log-rotation config (`docker inspect -f '{{json .HostConfig.LogConfig}}'`), and total log volume. Absence can reflect rotation; startup-line presence does not prove later capture. Do not present any candidate as the cause.
- Do not conflate with the earlier 2026-10-24 18:42 incident: that container WAS removed by preview-exec's own `docker rm -f` cleanup trap, which fully explains that earlier empty grep. The 19:57 failure is on a different, retained container and is unexplained.
- Standing direction: the operator start-failure diagnostics header (interview-engine PR #54, delivery-api PR #242) removes the dependence on engine stderr for this failure class. Separately, folding bounded read-only engine-log capture into the preview/diagnostic workflow was proposed; scope to be agreed before any work.

## 2026-10-25: The execution preview cannot run unmerged local changes (debug-loop gap)

- Incident: a fresh live retry (task c7e3f2522586, 2026-10-25 05:54:47Z) failed at engine start with "engine program failed during start: OperationalError". The user set the working rule: collect the exact error, make the smallest causal fix, verify it locally through the failing entry point, and only then open a PR - PRs and CI preserve a working change, they are not the debugging loop.
- Verified gap in current sources: every existing image path requires committed code. preview-exec.sh and build-local-image.sh abort unless all four checkouts (delivery-api, interview-engine, spec-generator, verification-loop) are clean, on main, and equal to origin/main (DIRTY/BRANCH/STALE guards), and the build git-archives HEAD, so uncommitted edits never enter an image. The scripts/docker test images likewise refuse tracked source edits. So a code change today forces commit -> push -> merge -> pull -> rebuild -> run before it can be exercised once.
- Image layout relevant to any fix: Dockerfile.full builds the four repos into wheels and pip-installs them; the launcher (scripts/docker/full_entry.py, copied to /app outside the wheels) spawns the engine as `python -m uvicorn interview_engine.http_service:app` from installed site-packages. All four wheels are single-package src-layout hatchling builds (delivery_api, interview_engine, spec_generator, verification_loop); console scripts exist but the container entry does not use them; no importlib.metadata/__version__ reads found in app.py, http_service.py, or full_entry.py.
- Candidate (NOT validated, not implemented): an opt-in local-overlay mode that bind-mounts each repo's src/<pkg> read-only over the installed package directory, skipping the clean/main guards only in that mode, with the startup banner honestly marking the running code as local-dirty rather than the main SHAs. Must be verified against the actual image (package paths, Python version, resources) and proven on the Mac before any PR. Not to be presented as supported until then.
