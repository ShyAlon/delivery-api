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

## A16: closed-gate draft/preflight fixtures

Database-backed gate-revision draft/preflight cases now use explicit scoped PG,
including the shared decision-page API helper caller. Pure producer-plan tests
stay DB-free. Every existing non-effectful/state/event/audit invariant,stale
approval/generation rejection,artifact digest validation and immutable conflict
assertion remains. No commit/restart execution or new adapter enabled. Fail-first
PG assertion failed on original SQLite preflight helper. Acceptance95passed
0skips6.74s with PYTHONPATH=.:src and producer sources:gate/decision pages,evidence,
native review/revision,closed revision jobs,cloud API,bootstrap. Production/UI
unchanged;hosted CI/full suite/deployed topology not run. A incomplete;B/C/D not
started.

## A17: closed-revision proposal queue fixture state

Queue cost-ack/draft retention and conditional-artifact preflight cases use scoped
PG. Existing mocked enqueue/capability/operation-guard boundary remains a unit
contract,not real dispatch authority or worker execution. Replace direct instance
assignment with monkeypatch so shared-store methods restore after each case.
All original cost-ack/no-source-mutation/guard/action/artifact assertions remain.
Phase-loop task titles are distinct because PG enforces per-owner uniqueness.
Fail-first PG type assertions failed on original queue SQLite. Initial PG run
caught repeated-title fixture collision;fixed inputs,not constraints.
Acceptance93passed0skips6.69s with root PYTHONPATH and producer sources:queue,
gate/decision pages,native review/revision/worker,cloud API,bootstrap. Production/
UI unchanged;hosted CI/full suite/deployed topology not run. A incomplete;
B/C/D not started.

## A18: remaining ProcessRunner contracts

Four mixed-module runner cases now use scoped PG:known progress count/missing
artifact fail-closed,stage-aware heartbeat,prompt output as diagnostics rather
than input wait,and traceback sanitization/missing-module classification. All
assertions unchanged. Local auth/rerun/factory cases in test_tasks remain legacy.
Fail-first PG assertion failed on SQLite. Focused25passed0skips6.63s with root
PYTHONPATH and producer sources (four runner cases,protocol/SSE/parity,native run,
bootstrap). Expanded mixed slice129passed2failed19.01s. Unchanged fresh main
mixed module108passed2failed12.85s,with the same legacy checkpoint-view and session
cost exact-response failures. Neither hidden nor weakened;separate debt.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A19: task management and gate provenance fixtures

Gate explicit-provenance and unique-name/rename/delete-cascade regressions use
scoped restricted PG. Management HTTP collision/rename/delete now uses real signed
cloud PG requests. Every original status/structured-error/audit/event/artifact
cascade assertion unchanged. Fail-first cascade PG assertion failed on SQLite.
Focused46passed0skips4.06s with producer sources:three cases,cloud API,native
lifecycle/gate,bootstrap. Expanded mixed-module slice151passed2same known baseline
failures16.20s (legacy checkpoint intent view and session-cost evidence metadata).
Not a full module green. Production/UI unchanged;hosted CI/full suite/deployed
topology not run. A incomplete;B/C/D not started.

## A20: gate ownership/final approval fixtures

Five gate/final-approval cases now scoped PG:explicit audited open-gate bypass,
final validation approval,recovery of final gate owner,parked gate precedence
and live engine decision precedence. Existing assertions unchanged. SQLite
schema-migration fixture remains backend-specific debt. Fail-first final-approval
PG assertion failed on SQLite. Focused56passed0skips5.43s with producer sources:
five cases,native gate/lifecycle,parity/protocol,cloud API,bootstrap. Expanded
159passed2known baseline failures17.39s (checkpoint intent view/session-cost
metadata);not full-module green. Production/UI unchanged;hosted CI/full suite/
deployed topology not run. A incomplete;B/C/D not started.

## A21: runner prompt/control boundaries

Five runner cases use scoped PG:question-line progress,open-gate missing-artifact
refusal,untyped blocking prompt stall failure,split envelope diagnostics and
untyped prompt refusal to create a control wait. Original assertions unchanged.
Fail-first question-runner PG assertion failed on SQLite. Focused29passed0skips
8.25s with producer sources:five cases,protocol/SSE/parity,native gate/run,
bootstrap. Expanded132passed2known baseline failures23.12s (checkpoint intent
view/session-cost metadata);not full-module green. Production/UI unchanged;
hosted CI/full suite/deployed topology not run. A incomplete;B/C/D not started.

## A22: runner lifecycle/stall/failure framing

Six runner cases use scoped PG:release-before-completion callback,silent child
stall failure,heartbeats reset stall window,split exception framing,correct
phase failure name and preserved contract-error cause. Every original assertion
unchanged. Synthetic children remain hermetic,no provider execution. Fail-first
silent-runner PG assertion failed on SQLite. Focused26passed0skips8.25s with
producer sources:six cases,protocol/SSE,native run,bootstrap. Expanded128passed
2known baseline failures18.48s (checkpoint intent view/session-cost metadata),not
full-module green. Production/UI unchanged;hosted CI/full suite/deployed topology
not run. A incomplete;B/C/D not started.

## A23: startup recovery ownership fixtures

Six startup/recovery cases use scoped PG:durable approved interview skip,
contradictory prefix repair,approved handoff to needs,durable external wait,
external wait over obsolete process noise,and legacy baseline pause ownership.
Existing exact recovered states,owner and no-spurious-launch assertions retained.
Continuation callbacks remain mocked;no real execution enabled. Fail-first skip
PG assertion failed on SQLite. Focused50passed0skips4.68s with producer sources:
six cases,native lifecycle/gate,parity,cloud API,bootstrap. Expanded152passed
2known baseline failures16.86s (checkpoint intent view/session-cost metadata),not
full-module green. Production/UI unchanged;hosted CI/full suite/deployed topology
not run. A incomplete;B/C/D not started.

## A24: active-work and typed-input fixtures

Seven cases use scoped PG:live orchestrator decision,subprocess/decision work
projection,external requirement projection,matching typed answer identity,visible
handoff failure preserving approval,shared handoff recovery,and long-history
requirement recovery. Exact owner/state/identity/event assertions unchanged.
Fail-first live-decision PG assertion failed on SQLite. Focused52passed0skips
5.69s with producer sources:seven cases,native events/projection/lifecycle,cloud
API,bootstrap. Expanded153passed2known baseline failures17.03s (checkpoint intent
view/session-cost metadata),not full-module green. Production/UI unchanged;
hosted CI/full suite/deployed topology not run. A incomplete;B/C/D not started.

## A25: task/input projection and stale answer fixtures

Five cases use scoped PG:stale-answer conflict/error evidence,live-input
projection,same-snapshot external owner restoration,rejected answer transcript
absence,and generated phase approval/audit provenance. Original assertions
unchanged;direct route helpers keep existing local policy. Fail-first projection
PG assertion failed on global SQLite. Focused55passed0skips5.42s with producer
sources:five cases,native projection/gate,decision pages,cloud API,bootstrap.
Expanded158passed2known baseline failures17.10s (checkpoint intent view/session
cost metadata),not full-module green. Production/UI unchanged;hosted CI/full
suite/deployed topology not run. A incomplete;B/C/D not started.

## A26: runner pricing durability fixtures

Durable phase price,incremental interview price before process exit,and
interviewer-prefix diagnostic cases now scoped PG. Exact token/price/audit and
no fabricated input-wait assertions retained. Synthetic usage and child scripts
only,no provider spend. Fail-first phase-price PG assertion failed on SQLite.
Focused30passed0skips3.98s with producer sources:three cases,native usage/provider
credit,model routes,protocol,bootstrap. Expanded135passed2known baseline failures
15.23s (checkpoint intent view/session-cost metadata),not full-module green.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A27: signed feedback CRUD/audit fixture

Full feedback create/upsert/target separation/list/update/missing/delete/audit
flow now uses real signed cloud PG. Every original assertion retained. Added
foreign-owner list/update/delete404 before the owner's successful update/delete.
Fail-first PG assertion failed on global SQLite. Focused49passed0skips4.99s with
producer sources:feedback flow,native review,cloud API,decision pages,bootstrap.
Expanded156passed2known baseline failures17.32s (checkpoint intent view/session
cost metadata),not full-module green. Production/UI unchanged;hosted CI/full
suite/deployed topology not run. A incomplete;B/C/D not started.

## A28: artifact projection/view fixtures

Five real-file artifact cases use scoped PG:session projection/ZIP,legacy
requirement-path session recovery,intent markdown/transcript,content-owned titles
and valid/invalid item-list evidence. Original assertions unchanged. Local file
HTTP policy explicitly retained;not cloud artifact access activation. Known
failing legacy checkpoint-transcript case untouched. Fail-first snapshot PG
assertion failed on SQLite. Focused52passed0skips with producer sources:five
cases,native artifacts,phase output,provenance,cloud API,bootstrap. Expanded
155passed2known baseline failures16.78s (checkpoint intent view/session-cost
metadata),not full-module green. Production/UI unchanged;hosted CI/full suite/
deployed topology not run. A incomplete;B/C/D not started.

## A29: orchestrator phase/model planning fixtures

Five orchestrator cases use scoped PG:bounded phase plan,greenfield architecture,
executor ownership/redo mapping,phase route override durability,and sticky exact
model/switch. Local policy explicit,runner calls still mocked. Every original
command/env/model/event assertion unchanged. Fail-first sticky-model PG assertion
failed on SQLite. Focused32passed0skips2.38s with producer sources:five cases,
model routes,native events/gate,cloud execution,bootstrap. Expanded135passed2known
baseline failures15.21s (checkpoint intent view/session-cost metadata),not full
module green. Production/UI unchanged;hosted CI/full suite/deployed topology not
run. A incomplete;B/C/D not started.

## A30: interruption and legacy-checkpoint refusal fixtures

Six cases use scoped PG:unbound pre-HTTP checkpoint incompatibility,shutdown
preserves resumable running state,legacy SIGTERM checkpoint refusal,parked-gate
no text replay,approved phase completion,and interview-preview downstream
isolation. Original assertions unchanged. Fail-first shutdown PG assertion failed
on SQLite. Focused19passed0skips2.97s with producer sources:six cases,native
lifecycle,protocol,native run,bootstrap. Expanded121passed2known baseline failures
16.84s (checkpoint intent view/session-cost metadata),not full-module green.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A31: runner decision/envelope and active-phase state fixtures

Five cases use scoped PG:structured founder options,fragmented stdout envelope
refusal,single active phase,downstream waiting reset,and following-You envelope
refusal. Every original assertion retained. Fail-first structured-options PG
assertion failed on SQLite. Focused21passed0skips3.55s with producer sources:
five cases,native projection/gate,protocol,seven-phase parity,bootstrap. Expanded
124passed2known baseline failures17.25s (checkpoint intent view/session-cost
metadata),not full-module green. Production/UI unchanged;hosted CI/full suite/
deployed topology not run. A incomplete;B/C/D not started.

## A32: session recovery and founder-reference artifact fixtures

Two cases use scoped PG:greenfield baseline correction recovers session from
recent requirement path after510 output events,and architecture founder refs
survive artifact validation/view. Every original assertion retained. Local
filesystem/orchestrator policy explicit,runner mocked as before. Fail-first
PG assertion failed on SQLite. Focused11passed0skips3.85s with producer sources:
two cases,legacy path recovery,native projection/gate,bootstrap. Expanded
116passed2known baseline failures16.40s(checkpoint intent view/session-cost
metadata),not full-module green. Dated receiver inventory classifies41 files:
14 migrate,16 guard,5 reference,6 retired-contract (mixed cases noted).
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A33: task diagnostics route fixtures

Model-call diagnostics with/without a recorded engine session use scoped PG.
Session binding uses the store method instead of direct SQLite SQL. Original
call fields,aggregate token/count/timing and absent-section assertions unchanged.
Explicit local diagnostics policy retained;not cloud execution-detail exposure.
Fail-first concrete PG assertion failed on SQLite. Focused55passed0skips4.23s
with producer sources:task/operator diagnostics,cloud API,bootstrap. Expanded
163passed2known baseline failures(checkpoint intent view/session-cost metadata),
not full-module green. SQLiteStore inventory falls41 to40 files,migrate14 to13;
other buckets unchanged. Production/UI unchanged;hosted CI/full suite/deployed
topology not run. A incomplete;B/C/D not started.

## A34: operator capacity/run-metrics auth fixtures

Two API cases now use signed cloud PG with real verifier,live auth session and
request DB instead of SQLite OAuth user/admin rows. Operator email claim is
signed by the hermetic fixture key;regular owner remains403,operator200. Added
anonymous401. All original capacity/metrics response assertions retained. Metrics
sample storage remains its separate SQLite module,not falsely claimed migrated.
Fail-first concrete PG assertion failed on SQLite. Focused46passed0skips4.11s:
capacity,cloud API,bootstrap. Expanded167passed2known baseline failures19.90s
with metrics and mixed tasks(checkpoint intent view/session-cost metadata).
SQLiteStore inventory39 files;primary migrate12,guard16,reference5,retired6.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A35: admin stop-run authorization fixture

Normal-user stop refusal uses signed cloud PG instead of SQLite OAuth rows.
Original403/no-stop-side-effect assertion retained;anonymous401 added. New signed
operator HTTP preview/confirm test checks preview changes nothing and confirmed
stop marks the synthetic in-memory run with operator attribution. No real
process/model execution. Existing direct local/thread/idle refusal and owner
notification tests unchanged. Fail-first PG assertion failed on SQLite.
Focused57passed0skips4.65s:admin stop,capacity,run slots,cloud API,bootstrap.
Expanded165passed2known baseline failures(checkpoint intent view/session-cost
metadata),not full-module green. SQLiteStore inventory38 files:primary migrate11,
guard16,reference5,retired6. Production/UI unchanged;hosted CI/full suite/deployed
topology not run. A incomplete;B/C/D not started.

## A36: waiting-input dedupe fixtures

Three remaining SQLite cases use scoped PG:two distinct adapters with concurrent
request transactions both receive the same durable event,duplicate preserves
original and invalid write leaves no changes,and input identity lookup past501
progress events. Existing PG foreign-owner/no-wakeup/updated-at assertions stay.
SQLite connection-specific transaction check translates to no retained operation
connection plus successful subsequent distinct write;durable original unchanged.
First-page bypass query uses scoped PG transaction,not SQLite SQL.
Fail-first PG assertion failed on SQLite. Focused11passed0skips2.70s:dedupe,native
events,bootstrap. Expanded119passed2known baseline failures(checkpoint intent
view/session-cost metadata),not full-module green. SQLiteStore inventory37 files:
primary migrate10,guard16,reference5,retired6. Production/UI unchanged;hosted CI/
full suite/deployed topology not run. A incomplete;B/C/D not started.

## A37: immutable interview schema binding fixture

Binding/idempotency/conflicting-version receiver now scoped PG,all original
assertions retained. Added independent-adapter persistence and foreign-owner/
missing-task refusal,owner unchanged after denied write. Legacy SQLite additive
binding-column contract remains explicitly unchanged,not silently removed.
Fail-first concrete PG assertion failed on SQLite. Focused43passed0skips:binding,
native projection,bootstrap,cloud API. Expanded151passed2known baseline failures
(checkpoint intent view/session-cost metadata),not full-module green. Inventory
37 SQLiteStore files unchanged because schema file retains legacy contract;
its primary bucket is now retired-contract,not migrate (9/16/5/7).
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A38: recorded simulation gate replay fixture

The remaining mixed-task recorded replay case uses scoped PG:imports bounded
usage,pauses at intent,records redo steer,continues past open CONOPS gate and
completes with artifact/audit/event evidence. Every original assertion retained.
No subprocess or provider execution. Fail-first concrete PG assertion failed on
SQLite. Focused17passed0skips2.32s:replay,simulation validation,native gate,
bootstrap. Expanded124passed2known baseline failures(checkpoint intent view/
session-cost metadata),not full-module green. SQLiteStore inventory37 files
unchanged because mixed tasks retains factory/legacy contracts. Production/UI
unchanged;hosted CI/full suite/deployed topology not run. A incomplete;B/C/D not
started.

## A39: HTTP-engine snapshot and typed-input fixtures

Eight cases now scoped PG:open-input snapshot,engine failure503,rationale
preservation/required refusal,text composer,structured choice,event dedupe and
waiting-without-input executing state. All original assertions retained. Local
HTTP-engine policy explicit,engine still fake. PG revealed fixed session 's'
collisions across owners:seed unique engine session per case and align fake
snapshot with it;do not bypass global session ownership. Fail-first PG assertion
failed on SQLite. Focused16passed0skips3.08s;expanded full mixed HTTP-cutover,
dedupe,bootstrap60passed0skips5.41s with producer sources. Remaining SQLite
receivers in HTTP-cutover retained. Production/UI unchanged;hosted CI/full suite/
deployed topology not run. A incomplete;B/C/D not started.

## A40: HTTP-engine terminal recovery fixtures

Eight terminal recovery cases now scoped PG:completed-session no replay,
independent-adapter restore/one-time handoff,missing/mismatched artifact refusal,
read repair,real orchestrator command planning with mocked runner,final answer
single submission,exact diagnostic disposition and legacy unpinned refusal.
Every original assertion retained. Unique fixture engine sessions preserve
ownership. Local orchestration policy explicit,no process/provider execution.
Fail-first PG assertion failed on SQLite. Focused11passed0skips2.04s;expanded
full mixed HTTP-cutover,dedupe,bootstrap60passed0skips with producer sources.
Remaining cutover SQLite receivers retained. Production/UI unchanged;hosted CI/
full suite/deployed topology not run. A incomplete;B/C/D not started.

## A41: failed-phase retry shared fixtures

Shared failed-Needs fixture and its architecture derivative now scoped PG with
unique session:12 callers cover preview/confirm,duplicate/ineligible/missing
artifact refusal,audit/old failure,generation/model selection,usage/cost dedupe
and failed launch. All original test assertions retained. Architecture helper
no longer overwrites task state with SQL:assert normal phase updates already
establish failed/architecture. Local policy explicit,mocked execution unchanged.
Fail-first concrete PG assertion failed on SQLite. Nine direct callers plus
bootstrap12passed0skips2.09s;full mixed cutover,dedupe,bootstrap60passed0skips6.06s
including all12 callers. Remaining cutover SQLite receivers retained.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A42: HTTP architecture ownership and phase-planning fixtures

Six cases now scoped PG:protocol422 versus transport diagnostic,architecture
local decision ownership/stale refusal,orphaned architecture recovery,canonical
phase configuration and sticky exact generator default. Every original assertion
retained. Unique session IDs/local policy explicit,runner/execution mocks remain.
Fail-first concrete PG assertion failed on SQLite. Focused9passed0skips1.83s;
full mixed HTTP-cutover,dedupe,bootstrap60passed0skips with producer sources.
Remaining cutover start/concurrency/switch SQLite receivers retained.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A43: HTTP Start and interruption fixtures

Eight Start cases now scoped PG:typed answer path,independent-adapter restart,
slow Start responsiveness,exact Flash/Phi4 provenance,async reserved identity/
duplicate refusal,interrupted Start classification and exact-session recovery
without second Start. Every original assertion retained. Local policy explicit,
fake engine unchanged. Fail-first PG assertion failed on SQLite. Focused
11passed0skips2.06s;full mixed cutover,dedupe,bootstrap60passed0skips with producer
sources. Three remaining cutover SQLite receivers:slow submit,live switch,real
registry blocked-advance concurrency. Production/UI unchanged;hosted CI/full
suite/deployed topology not run. A incomplete;B/C/D not started.

## A44: HTTP submit/switch/concurrency fixture closure

Last three HTTP-cutover SQLite receivers now scoped PG:slow submit stays
responsive,live model switch emits one durable event on repeat,and blocked real
engine registry advance leaves task/snapshot/progress readable. All original
assertions and .75s/1s/2s timing limits retained. Worker-thread task read enters
explicit tenant scope;switch double returns requested session. No SQLiteStore
reference remains in the module. Fail-first concrete PG assertion failed on
SQLite. Focused6passed0skips1.60s;full mixed cutover,dedupe,bootstrap60passed0skips
with producer sources. Inventory36 SQLiteStore files:primary migrate8,guard16,
reference5,retired7. Engine registry Program is synthetic,no model call.
Production/UI unchanged;hosted CI/full suite/deployed topology not run.
A incomplete;B/C/D not started.

## A45: API audit and queued human-error fixtures

Four cases now scoped PG:queued generation failure records actionable human
artifact,cross-phase totals,legacy-scale price exclusion with tokens retained,
and latest cumulative phase price wins. Every original assertion retained.
Local audit policy explicit;queue handler only raises a fixture UserFacingError,
no provider/process work. Fail-first concrete PG assertion failed on SQLite.
Focused8passed0skips1.69s:native run/usage,bootstrap and four cases. Full mixed
API,native run/usage,bootstrap,cloud API suite measured separately. SQLiteStore
literal inventory35 files,but API file still has implicit shared-bootstrap
receivers,so primary migrate remains8 rather than falsely marking API complete.
Production/UI unchanged;hosted CI/full repository/deployed topology not run.
A incomplete;B/C/D not started.

## A46: remaining API settings/session/preflight fixtures

Shared settings/session fixture and explicit unreachable-engine preflight cases
now scoped PG with concrete store assertion. All original assertions retained.
Local policy explicit,no real engine/model execution. Per-owner unique context
makes fixed task IDs safe;removed SQLite-specific absent-task pre-delete probes
(PG missing-task lookup raises). Four local link/budget cases stay on explicitly
named unchanged legacy fixture:local actor attribution is correctly rejected by
PG owner guard,two cases404 under a naive port. Reconcile in C,not bypass owner
checks. Fail-first shared client PG assertion failed on SQLite. Full mixed API,
cloud API,bootstrap56passed0skips4.39s with producer sources. API still incomplete
only for named legacy local contracts. Literal inventory unchanged35 files.
Production/UI unchanged;hosted CI/full repository/deployed topology not run.
A incomplete;B/C/D not started.

## A47: decision-review helper fallback closure

All seven decision-page helper callers already supply scoped PG. Remove the
unused optional workspace/default SQLite constructor so a future caller cannot
silently restore SQLite. Existing behavior assertions unchanged;poison test still
imports SQLiteStore deliberately. Failing-first source/signature guard rejected
the optional fallback. Decision pages,native review,cloud API,bootstrap49passed
0skips5.53s with producer sources. Literal inventory35 unchanged,but decision
pages is guard-only now (primary migrate7,guard17,reference5,retired7 across36
tracked receiver files including implicit API). Production/UI unchanged;hosted
CI/full repository/deployed topology not run. A incomplete;B/C/D not started.

## A48: stale SPA mount browser fixtures

Both real browser stale-mount cases now signed PG operator shell,not anonymous
SQLite subprocess. Seeded task and operator claim use existing hermetic PG key,
real middleware/session/request DB. Original stale operator/dashboard DOM,polling,
header identity and crash assertions unchanged. Failing-first source guard
rejected SQLite bootstrap. Focused stale mounts/bootstrap6passed0skips21.87s;
expanded stale mounts,decision pages,cloud API,bootstrap49passed0skips25.54s.
Final About pixels inspected:signed operator header intact,About active,readable
content,no stale dashboard underneath. Inventory35 literal SQLiteStore files unchanged:stale-mount source guard keeps
the token but no SQLite receiver. Primary migrate6,guard18,reference5,retired7
including implicit API contracts. Production/UI
unchanged;hosted CI/full repo/deployed topology not run. A incomplete;B/C/D not
started.

## A49: persistent SPA shell parity fixture

SPA shell suite now signed PG operator shell instead of SQLite subprocess.
Original navigation/header-node identity,delayed auth/fonts/cost/balance geometry,
mobile overflow,back/forward/deep link,unknown route,poll disposal,60s cost polling,
hidden-tab/debounce recovery and operator mount assertions unchanged. Existing
frontend auth mocks remain frontend contracts,not local auth activation.
Fail-first source guard rejected SQLite bootstrap. Full shell19passed0skips60.78s;
combined shell,stale mounts,cloud API,bootstrap60passed0skips83.00s. Dashboard
pixels inspected:readable task card/filter/new button,active Home,signed operator
identity and intact header. Product UI unchanged. Literal inventory35 unchanged
(source guard token);primary migrate5,guard19,reference5,retired7 including
implicit API. Hosted CI/full repo/deployed topology not run. A incomplete;
B/C/D not started.

## A50: shared task-browser dashboard/layout group

Six dashboard/brand/not-started/sticky-header/legacy-navigation/final-gate cases
use new signed PG fixture;remaining mixed UI callers retain explicit old fixture.
Five seeded task IDs are owner-qualified because PG IDs are globally unique.
All original assertions retained. Fail-first fixture signature guard failed.
Six cases+guard+cloud API+bootstrap45passed0skips17.17s. Pixels inspected:five
cards show waiting/complete/running/not-started states,regular-owner header,no
operator links. Existing V label/diamond overlap remains cosmetic debt below.
Production/UI unchanged;hosted CI/full mixed UI/full repo/deployed topology not
run. A incomplete;B/C/D not started.

### Existing cosmetic debt: V diagram label placement

Concept,Stakeholder,Verification and Validation labels overlap diamond markers
in dashboard screenshots. Confirmed visually in A49/A50 evidence and reported
by main. Existing product UI issue,not caused or fixed by fixture migrations.
Keep separate from fixture acceptance;do not claim diagram layout clean.

## A51: artifact viewer and audit browser group

Five artifact/viewer/audit cases now use signed PG browser fixtures and unique
owner-qualified task IDs. Existing UI payload routes remain presentation mocks;
these cases do not prove real artifact download content. Original behavior
assertions retained;tooltip visibility now waits for the CSS transition before
asserting. Signature guard failed first. Audit exposed a fixture teardown red:
lifespan-off uvicorn never notified active stream shutdown events. The fixture
now notifies them on its server loop before graceful drain;the thread-stop
assertion remains intact. Combined five cases+guard+cloud API+bootstrap passed
44 tests,0 skips in15.85s;repeat44passed,0skips15.08s. Viewer and open filtered
audit pixels inspected:readable headings/transcript,local close and download,
credit summary and one approve row. V diagram cosmetic debt remains. Production
unchanged;hosted CI/full mixed UI/full repo/deployed topology not run.
A incomplete;B/C/D not started.

## A52: interview history and feedback browser group

Four truncation,thumb/comment persistence/deletion,save single-flight/error
recovery and responsive comment-column cases use signed regular-owner PG.
History payloads remain explicit presentation snapshots;feedback writes,reads
and deletion hit the real PG-backed HTTP endpoints. Original assertions intact.
Signature guard failed first. Four cases+guard+cloud API+bootstrap43passed0skips
14.95s;final repeat43passed0skips14.63s. Pixels inspected:full question feedback
dialog,side-by-side desktop annotation and stacked600px annotation. Explicitly
scroll row into view before mobile screenshot (first capture was offscreen).
Debug/operator-menu history case remains separate for its authority contract.
Production unchanged;hosted CI/full mixed UI/full repo/deployed topology not
run. A incomplete;B/C/D not started. V diagram cosmetic debt unchanged.

## A53: founder decision dialog browser group

Seven structured-decision,Escape,outside-click/reopen,restart panel alignment,
four-option fixed controls,sticky ready-bar and answered/binary-approval cases
use signed regular-owner PG. SSE,task snapshots and submitted-answer responses
remain explicit presentation mocks,not proof of engine acceptance. All original
assertions retained. Signature guard failed first. Seven cases+guard+cloud API+
bootstrap46passed0skips24.18s. Pixels inspected:readable two-option decision,
selected PWA and rationale;scrolled four-option dialog retains title/compose
controls,selected Responsive web and last Hybrid option visible. Earlier cards
are outside scroll viewport by design. Production unchanged;V diagram cosmetic
debt remains. Hosted CI/full mixed UI/full repo/deployed topology unrun.
A incomplete;B/C/D not started.

## A54: answer composer browser group

Five failure/text recovery,16s no-timeout/cancel,keyboard submission,processing
shadow persistence and failed-send/no-shadow cases now use signed PG. Shared
_answer_page accepts task ID with legacy default for remaining callers. All
original assertions retained;slow-send explicitly sets no-preference motion
because its existing spinner-animation check cannot run under shared fixture's
reduced-motion default. Signature guard failed first;first migration run caught
that motion mismatch (43pass1fail),then five cases+guard+cloud API+bootstrap
44passed0skips32.41s. Snapshot/SSE/answer-response mocks remain presentation
contracts,not proof of engine input delivery. Pixels inspected:disabled answer
with spinner/cancel and submitting indicator;processing shadow preserves question
and answer. Sticky headings clip some upper content in scrolled screenshot;
not claimed fixed. Production unchanged;V diagram debt remains. Hosted CI/full
mixed UI/full repo/deployed topology unrun. A incomplete;B/C/D not started.

## A55: gate evidence and transition browser group

Seven checkbox in-place,evidence formatting,delayed transition,architecture
provenance,missing-artifact approval block,arriving evidence/input preservation
and desktop/phone help cases now use signed regular-owner PG with unique IDs.
All original assertions retained. Signature guard failed first. Seven cases+
guard+cloud API+bootstrap46passed0skips22.17s. Task/artifact/review/decision/SSE
mocks remain explicit presentation contracts,not real gate commitment proof.
Pixels inspected:desktop evidence/decision columns readable,formatted title
without visible UUID;phone stacks evidence and form,missing-artifact warning
and disabled approval visible. Phone help tooltip stays within viewport but is
translucent over checklist/buttons;sticky header crossing in full-page capture
also remains,not claimed fixed. Production unchanged;V diagram debt unchanged.
Hosted CI/full mixed UI/full repo/deployed topology unrun. A incomplete;
B/C/D not started.

## A56: live progress and heartbeat browser group

Nine functions (13 parameterized cases) now use signed regular-owner PG: stale
SIGTERM suppression,latest-command elapsed,title-case slots,old issue tooltips,
architecture stream progress,latest-stage headline,desktop/phone heartbeat wrap,
intent/needs collapsed/expanded progress and waiting authority over late
heartbeat. Original assertions retained;task/SSE mocks remain presentation
contracts,not real engine liveness proof. Signature guard failed first. Corrected
an edit spill into a neighboring decorator before collection;final13cases+guard+
cloud API+bootstrap52passed0skips36.39s. Pixels inspected:phone heartbeat wraps
within card;desktop active Stakeholder needs line matches expanded progress,
history and connection summary readable. Production unchanged;V diagram and
previous cosmetic debt remain. Hosted CI/full mixed UI/full repo/deployed
topology unrun. A incomplete;B/C/D not started.

## A57: browser error and retry recovery group

Seven functions (eight cases) now use signed regular-owner PG:orphan error,
protocol422 wording,legacy Ollama switch (running/failed),completed-session
switch suppression,failed Needs retry,environment-default retry model and
Architecture checkpoint retry. All original assertions retained. Signature
guard failed first. Eight cases+guard+cloud API+bootstrap47passed0skips21.92s.
Task/model/execution/retry responses remain presentation mocks;confirm submission
is intercepted,not paid execution or real checkpoint-resume proof. Inspected
Needs/Architecture dialog pixels:readable last failure,model choice,cost warning,
fresh versus checkpoint explanation and cancel/retry controls. Production
unchanged;existing cosmetic debt unchanged. Hosted CI/full mixed UI/full repo/
deployed topology unrun. A incomplete;B/C/D not started.

## A58: missing resources and flagged review browser group

Five resource file/path/format,recovery/Greenfield confirmation,single-flight,
flagged-input readable fields/blocked approval and interview review-summary
cases now use signed regular-owner PG. Original assertions retained. Signature
guard failed first. Five cases+guard+cloud API+bootstrap44passed0skips16.03s.
Task/artifact/semantic-review/resolve mocks remain presentation inputs;Greenfield
confirm is intercepted,not actual baseline mutation or paid execution. Pixels
inspected:layered Greenfield warning and Back/Switch controls readable;flagged
item uses human fields and blocked checklist. Full-page sticky-header crossing
partly covers scrolled gate heading,existing capture/layout debt not fixed.
Production unchanged;other cosmetic debt unchanged. Hosted CI/full mixed UI/
full repo/deployed topology unrun. A incomplete;B/C/D not started.

## A59: start and model-route browser group

Five start resolved-model/conflict,single-flight start,gate override defaults,
disabled route reasons and two admitted choices cases use signed regular-owner
PG. Original assertions retained. Signature guard failed first. Five cases+
guard+cloud API+bootstrap44passed0skips16.64s. Model/start/review responses are
presentation mocks;no real interview/model execution. Desktop and390px route
pixels inspected:two selectable choices,selected outline,disabled DeepSeek
reason and start button readable,no route-card overflow. Production unchanged;
existing cosmetic debt unchanged. Shared module now60PGfunctions/65expanded
cases,67legacy functions/72expanded cases,plus three standalone legacy-auth
browser cases. Refreshed inventory/forecast in receiver document. Hosted CI/full
mixed UI/full repo/deployed topology unrun. A incomplete;B/C/D not started.

## A60: task mutation browser group

Four filter/sort/rename/delete,keyboard create/cancel,pending-create single-flight
and mutation-without-navigation cases use signed regular-owner PG. Original
assertions retained. Real keyboard creation and rename/delete use disposable
tenant HTTP writes;pending-create and in-place snapshots/gate responses remain
presentation mocks. Signature guard failed first. Four cases+guard+cloud API+
bootstrap43passed0skips15.42s. Inspected create dialog:readable fields and disabled
Creating button;post-delete dashboard has four remaining cards. V-label overlap
and sticky-header crossing in full-page capture remain,not fixed. Production
unchanged. Shared module64PGfunctions/69expanded cases,63shared legacy functions/
68expanded cases,plus three standalone legacy-auth browser cases. Hosted CI/full
mixed UI/full repo/deployed topology unrun. A incomplete;B/C/D not started.

## A61: state/refresh/loading browser group

Five state reducer,SSE filter/coalescing,dashboard skeleton geometry,awaiting
route placeholder and immediate-error cases use signed regular-owner PG.
Original assertions retained. Reveal-animation case explicitly sets normal
motion;others keep reduced-motion default. Signature guard failed first.
Corrected malformed location.href JavaScript introduced during path rewrite;
final five cases+guard+cloud API+bootstrap44passed0skips19.34s. Snapshot/SSE/error
mocks remain presentation inputs;real delayed dashboard/task reads hit PG.
Pixels inspected:labeled skeleton and fixed shell visible;binary decision
heading/options readable. Loading screenshot retains About active navigation
while Home data loads,existing transient state not changed. Production unchanged;
existing cosmetic debt unchanged. Shared69PGfunctions/74expanded cases,
58legacyfunctions/63expanded cases,plus three standalone legacy-auth cases.
Hosted CI/full mixed UI/full repo/deployed topology unrun. A incomplete;
B/C/D not started.

## A62: stale/accepted decision browser state group

Eight stale-load/conflict,selected-concept context,accepted-event closure,
Needs-gate stale prompt suppression,rationale keyboard help,orchestrator
baseline and accepted-answer disarm cases use signed regular-owner PG. All
original assertions retained. Signature guard failed first. Eight cases+guard+
cloud API+bootstrap47passed0skips23.30s. Task/SSE/answer mocks remain browser
state contracts,not proof of actual engine/orchestrator acceptance. Inspected
selected PWA/concept reason and Approve/Reject pixels:readable and contained.
Production/cosmetic debt unchanged. Shared77PGfunctions/82expanded cases,
50legacyfunctions/55expanded cases,plus three standalone legacy-auth cases.
Repository size at base c839891:317test modules,2296test functions/methods by AST,
not collected/parameter-expanded tests. Literal inventory35files unchanged,
4migrate/19guard/5reference/7retired;implicit API adds one migrate file.
Hosted CI/full mixed UI/full repo/deployed topology unrun. A incomplete;
B/C/D not started.

## A63: interview layout and phase-change browser group

Seven Start-output visibility,internal history scroll/sticky Live process,
newest-first numbering,submitting normal/reduced motion and phase-change
pause/close cases use signed regular-owner PG. _long_interview_page accepts
unique task ID,legacy default preserved. Existing motion checks explicitly
start normal then test reduced mode. All assertions retained. Signature guard
failed first. Seven cases+guard+cloud API+bootstrap46passed0skips29.52s.
Task/answer/phase-output/SSE mocks remain browser contracts,not live execution.
Inspected phase transition heading/latest progress/background button and pinned
Live process/newest history. Upper content clipped by sticky header in scrolled
view remains,not fixed. Production/cosmetic debt unchanged. Shared84PGfunctions/
89expanded cases,43legacyfunctions/48expanded cases,plus three standalone
legacy-auth browser cases. Hosted CI/full mixed UI/full repo/deployed topology
unrun. A incomplete;B/C/D not started.

## A64: debug/account browser group

Five run-events desktop/phone,progress scope/account desktop/phone,avatar native
 tooltip,plain account actions,and copied diagnostic digest cases now use signed
regular-owner PG. Task URLs use isolated owner-qualified IDs. Existing auth
presentation mock remains in the progress/account case;it is not auth proof.
All original assertions retained. Signature guard failed first (1failure0.70s).
Five cases+guard+cloud API+bootstrap44passed0skips15.17s. Inspected phone account
menu and desktop copied Debug dialog. Full-page capture extends beyond viewport;
not a claim that every lower section was in the viewport at once.

Two attempted ports intentionally remain legacy:everything-ZIP's exact task-ID
assertion conflicts with UUID-like IDs being scrubbed to [token];classifications
is deliberately unavailable under cloud auth in identity.js. Initial seven-case
attempt6passed2failed46.87s. Neither assertion nor security policy was weakened.
These require separate contract/product resolution before migration.
Shared mixed browser89PG/38legacy functions out of127;three standalone local-auth
browser cases remain. Global conftest still bootstraps SQLite. A incomplete;
B/C/D not started. No production changes. Hosted CI/full suite/deployed topology
unrun. Presentation snapshots are not live engine execution proof.
