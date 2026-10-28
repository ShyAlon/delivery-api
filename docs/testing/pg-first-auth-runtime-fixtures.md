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

## A3: signed-out login/storage browser fixtures

Login layout (six viewport sizes) and remember-email tests use the signed-out real PG shell: clear the signed fixture cookie rather than enable local BASIC_AUTH. Remember-email deliberately stubs failed /auth/signin HTTP responses; this is a frontend storage/submission contract, not a test of Supabase credentials. Existing email persistence/overwrite/clear, no password/token storage, blocked-storage submission and layout assertions remain unchanged. Source guard failed before the fixture ports. Focused 11 passed in 14.97s; login/auth/runtime/delete/task-browser slice 26 passed, zero skips in 25.61s. Mobile 390x844 pixels inspected: fields and submit above fold, private-workspace copy readable, no horizontal clipping. Production unchanged. Remaining A/shared local UI/runner fixtures are not completed. Hosted CI/full suite/deployed topology not run.

## A4: debug/row12 browser fixture

Shared debug-progress and row12 suites use PG browser plus a hermetically signed operator-email token, not local-admin SQLite. Build identity is test-local app state from the same environment parser. Original fixture-ownership guard failed first. PG exposed a local-only row12_06 execution-read assumption: approved switch now asserts cloud execution/classification sections are policy_excluded without requests, and a separate permitted diagnostics failure asserts unavailable/read_failed with no raw private error. No operator execution-detail feature added.

The ZIP count assertion was already stale on main (actual9/10 vs pinned7/8). Approved fix derives available/unavailable counts from the downloaded evidence manifest plus the bounded five rendered sections, retains inline-error/known-omissions assertions, and requires the deliberately offline server-log section in missing outcomes. It does not call policy exclusions read failures. Focused 10 passed in12.21s; debug/row12/login/runtime/browser slice34passed0skips53.80s. Pixels inspected show sanitized unavailable/read_failed JSON and no private error. Production unchanged; full suite/hosted CI/deployed topology not run. Remaining A/shared UI/runner fixtures and prior debt unresolved.

## A5: model-route helper fixtures

Model-route tests now use a per-test restricted PG workspace and tenant context,
with module/app-state/orchestrator stores patched together. SQLite opens raise.
Exact-selection persistence is still checked through a separately constructed
store, now a second restricted PG store rather than a reopened SQLite file.
All existing transcript/model/fallback/pinned-state/error-disclosure assertions
remain. These direct-helper tests retain their original local policy mode; this
is storage migration, not a claim of cloud execution-detail access or new auth.

Fail-first storage/role contract failed on the old module SQLite store. Acceptance:
60 passed, zero skips in 4.48s with both producer sources enabled (model routes,
PG bootstrap, projections, run usage, event progress, provider credit, cloud API).
No visual product or UI change. Hosted CI, full suite and deployed topology not
run. Global conftest and remaining shared local fixtures still need A work.
B/C/D not started.

Preview-smoke inspection is parked separately: its old fixture wraps a local
shell in cloud middleware to reproduce three anonymous 401 probes. The actual
signed-out cloud PG shell issues none of those probes. The exact-probe assertion
cannot simply be migrated without deciding the intended contract. No preview
code or assertions changed in A5.

## A6: progress protocol and runner fixtures

Progress protocol tests now use restricted PG with per-test tenant scope and
SQLite opens poisoned. Module/app-state/orchestrator stores stay aligned. The
restart-dedupe test still constructs an independent second store against the same
database. Existing cursor/control isolation, stale gate rejection, successful
child without artifact failing closed, live engine-start progress ingestion,
every generator phase's artifact/gate boundary and measured ingest timing
assertions are unchanged. Synthetic child scripts and temporary artifact files
remain test fixtures, not provider execution or paid subprocess activation.

Fail-first contract errored because the PG fixture was absent. Migration first
caught a late app import trying to open SQLite under poison; app import and store
patching now occur before poisoning. Acceptance:42 passed,zero skips in7.23s,
both producer sources enabled (protocol,event-driven stream,seven-phase parity,
native event/gate/run/projection,model routes,bootstrap). Production unchanged;
no UI change. Hosted CI/full suite/deployed topology not run. A still incomplete;
B/C/D not started.
