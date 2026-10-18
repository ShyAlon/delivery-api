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
