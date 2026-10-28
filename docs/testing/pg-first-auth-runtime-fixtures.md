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

## A7: event-driven SSE and complete phase parity

A shared pg_scoped_workspace direct-helper fixture aligns module/app-state/
orchestrator stores, enters the tenant scope and poisons SQLite opens. SSE tests
now exercise PG for off-loop reads, write notification, post-commit visibility
and unchanged bounded read/heartbeat cadence. The raw-thread commit observer
explicitly enters the same tenant scope because OS threads do not inherit
ContextVars; no production bypass or unscoped role is used. Complete phase parity
keeps every progress dedupe, gate revision/approval and final done assertion.
Pure event-bus/cache/Node cases do not acquire a database unnecessarily.

Fail-first fixture contract errored before the fixture existed; focused10passed.
Expanded acceptance81passed0skips16.70s with both producer sources enabled:
protocol,SSE,phase parity,native event/gate/run/projection,model routes,bootstrap,
cloud API,delete/task browser. No UI or production changes. Hosted CI/full suite/
deployed topology not run. A remains incomplete;global SQLite bootstrap and other
legacy fixtures still need reconciliation. B/C/D not started.

## A8: next-phase admission fixtures

The completed-interview fixture and unfinished-interview rejection use the shared
restricted PG scope. All existing claim-once, next-state, event and rejection
assertions remain. Pure eligibility/credit checks and mocked admission/route
contracts stay unchanged. The new fixture contract failed first on SQLite.
PG then exposed the fixture's reused engine-session primary key across tenants;
use an owner-derived unique session, retaining actual begin/reconcile operations.
Acceptance86passed0skips9.47s with producer sources enabled:phase start,cloud
execution/API,protocol/SSE/phase parity,native lifecycle/gate/worker,bootstrap.
Production/UI unchanged. Hosted CI/full suite/deployed topology not run.
A remains incomplete;B/C/D not started.

## A9: signed task-poll observability

The one-combined-snapshot task GET regression now uses real signed cloud PG
requests instead of opening SQLite then disabling cloud mode to avoid auth.
Original 200/one-read/empty-events assertions retained; added anonymous401 before
any snapshot and foreign-owner404 through the same scoped snapshot. Other
observability/Node/static wiring tests unchanged. Fail-first PG type assertion
failed on the original SQLite fixture. Acceptance60passed0skips7.74s with both
producer sources enabled:observability,bootstrap,cloud API,SSE,native projection.
Production/UI unchanged. Hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A10: recorded replay restart fixtures

Recorded replay restart uses a second independent restricted PG store,not the
original object. Original interruption/failure/idempotency/delete,regular-task
preservation and partial-recording terminal-state assertions remain. SQLite
opens poisoned through the scoped fixture. Added proof that reconciliation in
one owner scope cannot stop another owner's running replay. Local recording
files/synthetic replay remain hermetic;no provider calls or cloud replay API
activation implied. Fail-first restart PG assertion failed on original SQLite.
Acceptance74passed0skips9.51s with both producer sources:restart,simulation
validation,protocol/SSE/parity,native lifecycle/run/projection,cloud API,bootstrap.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A11: decision evidence fixtures

Founder/model provenance and immutable retained-review helper regressions now
use scoped restricted PG with SQLite opens poisoned. Exact founder source turns,
model-only empty evidence,idempotent replay and conflicting-revision rejection
assertions unchanged. Provenance files use per-owner session IDs. Added independent
store reopen and foreign-owner invisibility checks for retained evidence.
Fail-first retained-review PG assertion failed on original SQLite. Acceptance
47passed0skips3.97s with both producer sources:provenance/immutable reviews,native
review/revision journal,cloud API,bootstrap. No UI/production change;hosted CI/
full suite/deployed topology not run. A incomplete;B/C/D not started.

## A12: simulation import/validation fixtures

Simulation validation uses scoped restricted PG for module/app-state/orchestrator
and a fresh Simulation instance per test. Existing local replay HTTP contracts
remain explicitly local;cloud policy unchanged. Direct import tests use the same
PG store. All title override/fallback,canonical intent resolution,upload errors,
ASGI replay startup and structured failure assertions retained. SQLite opens
poisoned;new contract checks real backend role and shared Simulation store.
Fail-first store contract failed on original global SQLite. Acceptance77passed
0skips9.93s with both producer sources:simulation/restart,cloud API/execution,
protocol/SSE/parity,native lifecycle/run,bootstrap. Production/UI unchanged.
Hosted CI/full suite/deployed topology not run. A incomplete;B/C/D not started.

## A13: engine failure and operator diagnostics fixtures

Engine-unreachable local route tests and operator diagnostic roundtrip/validation/
customer-surface exclusion use scoped restricted PG. Engine mocks remain failing
transports;the route tests explicitly retain local policy,not cloud admission.
Existing actionable503/error guidance,no-event-before-preview,view fallback,
presence-only blocks and bounded-page assertions unchanged. Engine session IDs
unique per owner. Fail-first diagnostic-store PG assertion failed on SQLite.
Acceptance67passed0skips4.37s with both producer sources:engine failure,operator
diagnostics,cloud API/execution,bootstrap,native projection/events,observability.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A14: phase-output freshness fixtures (partial migration)

Five independent previous/current/empty/invalid/redo freshness cases use scoped
restricted PG;local file-backed HTTP policy retained,all original assertions
unchanged. SQLite opens poisoned. Fail-first PG store assertion failed on SQLite.
The attempted sixth rerun case fails because PG deliberately rejects legacy
prepare_rerun until immutable generation/session copy policy is implemented.
That original test is moved verbatim into test_phase_output_legacy_rerun.py,
clearly labeled SQLite-only debt,not removed/skipped/emulated. This is not proof
of complete phase-output migration or cloud file-output API support.

Acceptance72passed0skips5.43s with producer sources:output plus retained legacy
rerun,protocol/parity,native projection/gate,cloud API,bootstrap,model routes.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started. Legacy rerun needs explicit contract reconciliation.

## A15: retained decision-review API fixtures

Decision-review API cases pass a scoped PG workspace explicitly to their shared
setup helper. Unmigrated helper consumers keep SQLite,not silently converted.
Existing local HTTP policy remains explicit. Unique session IDs stay bound in
input/semantic evidence;all retained-review,draft/comment,stale submit,explicit
purpose and same-sequence conflict assertions unchanged. PG contract checks role
and poisoned SQLite. Fail-first type assertion failed on old SQLite helper.
Acceptance82passed0skips5.77s with PYTHONPATH=.:src and both producer sources:
decision pages,legacy gate revision callers,provenance/immutable,native review/
revision,cloud API,bootstrap. Initial invocation without repo-root PYTHONPATH
failed existing tests.* imports;corrected command includes root explicitly.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.
