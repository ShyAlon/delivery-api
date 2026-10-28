# A: PG-first runtime/auth fixtures (2026-10-28)

Production is unchanged. This first fixture PR adds a restricted-role PG process environment and actual app import contract; it poisons SQLite/Turso opens, refuses any DSN except its own disposable server, and uses an uncreated home/data path. PG's URI is constructed from the local harness conninfo rather than incorrectly treating libpq key/value conninfo as an application URL. No provider/JWKS network call is needed for this bootstrap.

The existing full cloud API suite now uses locally signed ES256 JWTs, the real Supabase verifier and live-session check/request database instead of a/b verifier tokens. Cookie CSRF assertions retain their exact response expectations using signed cookies. Shared pg_task_app now patches the orchestrator store along with module and app state. Delete-dialog browser regression moves from SQLite subprocess/anonymous local admin to authenticated restricted PG; modal/real-delete assertions stay unchanged. New signed tests reject expired, absent, cross-owner and revoked sessions, advancing only the test cache clock past the approved 30-second revocation TTL.

Failing-first runtime fixture contracts originally fail for missing fixture; poison bootstrap then found malformed key/value DSN selection and passed after URI construction. The auth mismatch found during this work was fixed separately in merged PR346, not hidden in A.

Acceptance: 282 passed, zero skips in 98.02s, with both producer sources enabled. Runtime/API/delete-browser focus: 39 passed. Could not run hosted CI, deployed auth/config topology or the full repository suite. Existing static UI wiring and combined-order expense-guard debt remains unresolved. No deploy/restart/live DB/spend/phase5.

## Remaining fixture migration scope

This PR does not claim every legacy fixture is migrated: a literal source inventory still finds 57 test/helper files with SQLiteStore or sqlite:/// references (this is candidate count, not receiver-resolved runtime reachability). Local basic/OAuth behavior tests remain until C retires those behaviors; SQLite evidence/reference fixtures remain for D's consumer proof. Many legacy UI/runner tests use shared test_ui_playwright subprocess fixtures and local filesystem/account assumptions. They need further PG fixture migration before a full-suite green claim under the PG-only default. B can use the explicit new process environment, but changing global conftest bootstrap still needs this remaining migration reconciled, not concealed by skipping tests.

Pixel inspection of migrated delete regression's final dashboard confirms an empty task list, signed-in account indicator, no stuck modal and readable page controls. This validates the browser test outcome, not deployment behavior.

## A2: typed decision/validation browser fixtures

The typed decision-page suite and validation-panel suite now use pg_browser instead of separate anonymous local subprocess app/database bootstraps. Existing mocked decision payloads remain frontend fixtures, not a claim of real decision execution. All decision/draft/history/stale submission/layout assertions are unchanged. Fail-first source guard rejected both local bootstraps before migration. After migration: 40 passed, zero skips in 89.96s. Follow-up PG runtime/delete/task-browser/ownership/validation slice: 9 passed in 10.04s. No production code change. Pixel inspection shows signed-in review controls and readable evidence; the screenshot is captured at its existing scrolled position with the sticky review header overlapping the top of content, not claimed as a new visual-layout fix. Shared test_ui_playwright and other legacy fixtures remain for later A work. Hosted CI/full suite/deployed topology unverified.
