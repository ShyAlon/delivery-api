# Database-only storage work in progress

Base: merged main f23feb2 (runtime parity #671).

This branch is a recovery checkpoint, not merge-ready. No deployment, server
contact, migration, or paid execution is part of this work.

## Current evidence

- Unit 1: generator manifests require owned retained documents, with no host-file
  fallback. Failing regression reproduced before the change.
- Child generator suite: 13 passed on the rebuilt code.
- Unit 2: canonical producer/revision readers have no file fallback. PG reader
  and journal tests: 33 passed; revision stage/job/intent: 27 passed.
- Unit 3: artifact snapshot/output/view/download/ZIP and session list/bundle
  endpoints use only PG. Seven policy cases failed before removal.
  Route/byte/package group: 66 passed. Additional authenticated hostile-folder
  regression is included.
- Full PostgreSQL store run timed out without a final result and is unverified.
- CI unavailable, local gate only. Full theme gate has not run.

## Still to audit and implement

- Artifact reads, downloads, bundle exports and settled publication must use PG.
- Remove restore/backfill and filesystem publication paths once replacement
  coverage is verified. No old-session import or migration.
- Fresh producer inputs and revision evidence/proposals/install must be PG-only.
- Retain recording imports and replay bytes in PG; discard upload staging.
- Customer diagnostics must read retained evidence and preserve privacy.
- Remove local-auth and location-selector branches without bypassing signed
  identity, tenant RLS, owner admission, balance, ceilings, Stop or quiescence.
- Retire only obsolete tests, replacing them with current real PG guarantees.
- Full local core, PostgreSQL, installed-runtime, fresh-start, browser and build
  gate; final diff review before opening one theme PR. The owner merges.

Previous environment results do not count as proof for this rebuilt branch.

Unit 4: retired host folder/interpreter setting routes, 57 signed route/policy
tests passed. The underlying uncalled module awaits dead-code removal with its
remaining legacy tests; no runtime setting is written by HTTP anymore.

Unit 5: coordinator uses PG documents, fence and publication unconditionally.
31 non-SDK coordinator/recovery tests passed; 66 child/document/fence tests passed.
Eight SDK transport variants remain unresolved: first failure is the fixture's
missing operator expense policy (also reproduced before this edit). Production
money checks remain intact. Full gate is not green and no PR is ready.

Unit 6: imported recording rows/artifacts retained in PG; replay survives source
folder loss. HTTP upload staging is temporary and cleaned. Replay/import selected
group: 14 passed. Public replay remains guarded, unchanged.

Unit 7: parent runner requires registered child and PG evidence, with no file
pricing or publication fallback. Actual child + installed all-phase fake-provider
journey + fresh zero-credit startup group: 17 passed. No live model call.

Unit 8: signed tenant identity only; no local admin/basic/OAuth fallback. Signed
identity, routes, sessions and operator-startup group: 84 passed. Remaining
compatibility tests still need conversion; no full-gate claim.

Unit 9: gate decisions/lineage use PG transactions, not host paths. Signed gate,
revision, stack and identity group: 51 passed. Unit fixtures queue only; provider
execution proof remains the separate installed journey.

Unit 10: installation coordinator uses PG destination lock, anchor and promotion
receipt only. Policy regression failed first; 40 journal/plan/safety/staging tests
passed, plus 5 real PG installation/crash/tamper/rollback scenarios. The old
filesystem helper module remains to extract its pure plan validator then retire.

SDK fixture gap resolved: fake wire tests now provide a real saved operator policy
and matching 10^12 quote unit scale. All 8 SDK variants passed. This changes only
disposable test policy, not production admission. No live provider contact.

Unit 11: supplemental classification, execution and provenance readers use PG
only, with no host debug glob, pinned-route file or unbound session-price read.
Five policy regressions failed first. Signed classification browser included in
71-test provenance/documents/debug/policy group. Chromium installed locally for
remaining browser gate. Full gate attempt was interrupted at 15%; no full-pass
claim, known compatibility fixture failures remain.

Unit 12: removed host bundle discovery/root settings/projection. Retained bundle
summaries preserve actual pending/error/warning/approved interview states and
project title, completion and validation from PG. Red-first retired-function
policy; 5 selected bundle/session tests passed, signed routes included.

Unit 13: retired restore/backfill modules, backfill entrypoint and file publication
helpers. Their tests were removed with the retired behavior, not skipped. Current
PG validator provenance and settled changed/vanished/appeared/current/identical
cases replace the old file tests. 94 document/manifest/byte/policy tests passed,
including failed publication atomicity and worker-attempt fencing. No import or
migration route remains in these modules.

Unit 14: extracted the pure exact installation-plan validator into the plan
module and removed the unused file-lock/anchor/rename installation module.
Removed uncalled approved-upstream file readers; PG source/stage tests own that
contract. 40 revision plan/journal/safety/stage/policy tests passed. No route or
installation authority is added.

Unit 15: removed dead auth/OAuth/quota settings and artifact rollout selectors.
Task-read cache key is always owner/session scoped and has no artifact selector.
Cache TTL behavior is unchanged. Red-first absent-setting policy; 97 signed auth,
manifest, child cutover, document and runtime-contract tests passed. Tests that
set retired flags now explicitly create ignored attributes to verify no effect.

Unit 16: model-call diagnostics project retained PG bytes, not a host jsonl path.
Signed route survives absent host directory; duration-only customer projection
and account-debug token detail remain separate. Red-first path-policy regression;
29 diagnostics/debug/policy tests passed. Signed classification desktop/mobile
screenshots inspected: retained verdicts are readable, dialog scrolls correctly.

Unit 17: removed unused host Python discovery/settings module and its obsolete
interpreter selection tests. Installed child/contract checks cover the one Python
runtime. Updated signed cloud/session/cache fixtures for retired bundle APIs and
owner/session cache key. Both suspended semantic and stack PG mutations fail at
the suspended-owner guard before evidence changes. Signed start error remains
human-readable and leaves task unstarted.

Unit 18: removed store-level path publication adapters. Single-member and complete
revision publication now take exact bytes only. Existing tenant isolation,
validation hash/provenance, CAS race and rollback assertions retained in converted
fixtures. Red-first absent-adapter policy; 47 artifact/native/evidence tests passed.

Unit 19: retry/handoff/audit/cost use retained PG artifacts and usage only. Retired
local retry dispatch tails and cost rollup filesystem writes. Red-first no-file
policy; 121 phase/retry-runner/gate/cost-field tests passed. Customer cost guards,
lineage rollback and review-revision checks remain exercised.

Unit 20: removed uncalled app path-artifact and bound-text helpers. Missing and
hostile-symlink residue cannot become downloads; selected signed cloud,
observability, provenance and session-cost fixtures use only PG evidence. No
change to legacy raw runner fixture failures still awaiting conversion.

Unit 21: final package create/download routes no longer have host package paths
or a file-handoff fallback. Customer provider-provenance exclusion is identical
across targets, not selected by location. Red-first package route policy; source
helper settings/provenance tests updated to current boundaries. Prior unit20
selected run was 142 passed, 3 failures: two obsolete helper fixtures corrected
here, one raw runner fixture still pending. No full-pass claim.

## Checkpoint 2026-11-04 09:24 IST

Fresh final runtime group after all unit21 code changes: 54 passed in 59 seconds
(SameHostProducerWorker including all 8 SDK variants; actual child generator;
installed all-phase fake-provider task through final download; fresh zero-credit
HTTP start). This is not a paid-provider test and not the full gate.

Remaining before the theme PR:
- Replace legacy raw CLI ProcessRunner fixtures with registered child/capability
  fixtures, retaining diagnostics/progress/Stop/error/slot release assertions.
  tests/test_tasks.py latest complete run: 30 failures, 64 passes before later
  bundle fixture repair. Three session-cost cases now pass. Do not delete runner
  behavior tests simply because old arbitrary subprocess entry was removed.
- Audit remaining constant CLOUD_TENANTS selectors in app/runner and remove the
  unselected compatibility paths carefully. Constant is True, no environment
  selector, but dead code still obscures the one-runtime contract.
- Debug ZIP has owned PG document reader in production but still optional file
  fallback in helper. Ephemeral bounded server process logs are intentional,
  separately reviewed in theme1. Do not misclassify them as durable task storage.
- Retire unused pricing filesystem helpers after reference/test audit; keep pure
  rate/token arithmetic and PG usage/ledger guards.
- Review diagnostics, retry/handoff details and source tree for remaining host
  reads; packaging scratch and immutable build metadata are intentional.
- Full 4000+ test collection has not completed. Broad A-F timed out at 38%; its
  partial dots are not proof. Split browser/integration/core batches with durable
  JSON/JUnit results and appropriate per-process bounds. Browser runtime installed.
- Complete standalone PostgreSQL store run in bounded parameter groups; original
  combined timeout is still unverified. Five actual install/crash/tamper/rollback
  scenarios passed after PG coordinator edit, but other parameters remain.
- Browser screenshots for classification desktop/mobile were inspected and sent
  to parent. Other full browser/build gates remain. No theme PR exists.

All meaningful units are pushed. No deploy, server contact, paid provider, live
DB mutation, migration, merge or PR was performed.

Unit22 WIP fixture conversion checkpoint: tests/registered_runner.py registers a
real disposable child stdout stream after creating its real owned session, and
the unchanged runner still validates owner/capability/output namespace. No provider
or publication bypass. 24 existing diagnostic/lifecycle tests use this fixture;
21 initially passed, three failed. Stdin EOF fixture fixed next; completion
callback assumptions still need current queued-continuation assertions. This is
an unfinished test-fixture unit, not a gate pass or production behavior change.

Unit22 partial follow-up: 23 retained runner diagnostic/control/lifecycle tests pass
through registered owned child fixture. Replaced legacy implicit interview
callback expectation with explicit no-auto-start after approved interview and
released process slot. Admitted next-phase dispatch failure is visible, preserves
approved upstream phases and retains pending job for explicit recovery. This
matches phase_start's click-only policy. Explicit open-generator-gate automatic
continuation remains separately unverified; do not claim its acceptance yet.

Correction: the new admitted next-phase failure fixture initially returned no job; its prerequisite setup is being fixed before counting it as passed.

Verified after fixing the first-start fixture: all 24 selected tests pass (6 seconds). Prior 23/1 result was not a completed gate.

Unit23: migrated the two retired runner-file pricing assertions to the actual
once-only retail ledger and signed live-cost route. Verifies retained token
counts and exact 10-credit total under replay, incremental total while the worker
is still running, and unchanged worker states. Built-runtime unavailable-engine
assertion now uses real signed identity and declared installed routing; still
proves typed default preflight failure and zero legacy runner calls. Combined
test_tasks/retail projection/ledger: see actual local output below.
108 passed, 1 warning in 28.93s

Unit24: removed constant CLOUD_TENANTS branch selection from app, runner,
orchestrator, engine client, dispatch, debug collector and namespace authority.
The signed/PG/owned child path is unconditional. Removed unreachable alternate
public rerun/health/operator views and event-derived session recovery. No public
capability added. Narrowed dispatch to installed child engine only. Four legacy
fixtures now assert no unbound recovery, full empty input state, canonical owned
paths, and no subprocess fallback. Combined group: 164 passed, one source-string
spacing failure; corrected that assertion and all 10 inprocess tests pass.
Full gate and dead-function audit remain outstanding.

Unit25: retired file usage/pricing/rollup helpers and the unused bound file reader.
Pricing calculation is now pure price_rows; production rate helpers unchanged.
Malformed, non-object, missing, numeric-invalid and all-invalid rollup evidence
assertions now run through retained PG documents, including the signed unknown
cost endpoint and preservation of raw bad bytes. test_tasks + usage evidence +
incremental projection: 104 passed. The one session-fixture collision discovered
on the first run was fixed with unique disposable session IDs.

Post-unit25 installed child/producer/fresh HTTP acceptance re-run: 54 passed in
59 seconds, fake providers only. Removed the provably unreachable legacy
read-triggered auto-handoff branch from get_task. HTTP cutover fixture migration
is still unfinished (latest bounded run stopped after five failures, nine passes);
old auto-start expectations are not current acceptance. No partial run is a gate.

Current whole collection: 4051 tests, 442 files. The alphabetical a-c bounded
sweep reached 57% before its 95-second timeout and showed failures; no aggregate
pass count is claimed. Signed identity + Stop routing/owner/admin tests:18 passed.
Two Stop fixtures now prove flipping the retired mode constant cannot disable
the actual process/ownership checks (same 409, not a local-only 404).

Unit27: account/ledger/release/operator/build-source fixtures now assert the
unconditional signed interface rather than the retired local bypass: 76 passed.
Seventeen child/runtime files were run individually with complete results: all
passed (see bounded per-file results; overlapping earlier runtime evidence).
The a-c first-failure-per-file sweep is incomplete; audit API fixture still
expects private token detail on the customer surface. No full-gate claim.

Unit28: replaced removed file-reader race tests with real PG reader revocation
and hostile replaced-host-directory evidence; all 48 cloud API tests pass.
Extracted the existing private audit-total calculation without changing it;
three detailed accounting assertions retain exact token/pricing semantics while
signed customer route assertions explicitly verify credits-only disclosure.
Those three audit tests pass. Legacy public-link budget wrappers still fail.

Unit29: diagnostic ZIP content requires an owned PG document reader, with no
file-content fallback. Bounded scoped ephemeral process-log tails remain the
intentional theme1 exception. Secret redaction, malformed JSONL, verification
scores, per-source size, omissions, exact log identity and real account capability
checks run unchanged through PG evidence:18 passed. All 15 first d-group files
also finished individually green, including DB-only policies and debug helpers.

Unit30: all 15 API tests now pass. Legacy public link/run endpoints remain
unavailable even to signed identity; their old budget assertion now calls the
existing scoped usage accounting directly and still proves 402, budget_stopped,
and retained $7.50 spend. No production legacy route was reopened. Detailed
accounting is still tested privately and public audit is still credits-only.

Unit31: engine-unreachable and stale decision review tests now enter the signed
HTTP boundary with declared installed routing, never unsigned local wrappers.
Transport/down-engine errors and stale review 409 preserve their assertions;
12 tests pass. The old wrapper helpers are not counted as acceptance.

Unit32: all 56 HTTP cutover tests pass through real signed tenant HTTP instead
of subprocess local-mode wrappers. Fixture transport explicitly declares fake
reviewed model pin/installed engine; dispatch still uses real durable claim and
execute_job, not direct alternate production execution. Slow start/submit tests
retain responsiveness checks under actual auth. Interview finish and reads do
not auto-start Needs. Public model switching remains 503; internal adapter still
tests two identical replies produce one event and exact sticky default.
Retry tests retain upstream/generation/checkpoint/duplicate/failure assertions.
Private audit totals retain exact token/detail assertions, public audit credits
only. Browser decision/backoffice shared UI full files:50 passed in99 seconds,
so the earlier 22-second per-file timeout was not a test failure.

Unit33: export/observability fixtures now exercise PG bytes and installed dispatch
instead of file refs or retired local execution. Unavailable rerun remains 409;
21 parity/observability tests pass. The 45-file bounded group completed:
286 passed,1 skipped,9 failures. Two observability and export failures fixed
here. Model-route/private-detail fixtures still need migration. Private full graph
re-run with required sibling source found an actual stale test-only script import:
scripts/item6/pipeline_executor.py imports removed compute_meter.excluded_wait at
lines69/148; unresolved, not a timeout or passed acceptance. Installed production
child graph acceptance is separate and previously green, but final recheck remains.


Continuation checkpoint, 2026-11-04 10:01 local:
- Latest code checkpoint2616fe9, pushed. No PR/deploy/server/paid model/live DB
  writes or migration/merge. DB mutations in tests use disposable local harness.
- Remaining full gate:4051 collection. Many bounded groups overlap; never sum
  them into a whole-suite result. 45-file follow-up group finished370 passed,
  31 skipped,4 failed:operator_pages ZIP, ops_health admin data/download,
  ops_health shell, owner_acceptance_real_entry15s timeout. Inspect real output
  before classifying timeout. Older 286/1skip/9fail group:export and two marks
  fixed21pass; private graph source env exposed stale compute_meter import;
  model_routes five private-provenance/pin fixtures still need migration.
- HTTP cutover56pass; API15pass; cloudAPI48pass; signed failure/review12pass;
  diagnostics18pass. Browser decision+backoffice shared files50pass99s, so
  per-file22s timeout is insufficient for long browser files. Actual installed
  production child+same-host+fake SDK+fresh HTTP54pass59s on82866fd; recheck
  after remaining production edits before opening theme2 PR.
- Open-generator-gate automatic continuation remains explicitly unverified.
  Runner's old continuation variable has no consumer. Do not restore retired
  callback path or silently claim preservation; ground expected current feature
  policy and test installed admitted lifecycle. Interview is explicitly click-only.
- Dead source audit:app legacy recovery functions remain uncalled from startup;
  runner dead pricing fields/on_complete/continuation variable; config constant
  CLOUD_TENANTS retained for test compatibility but no production branches except
  startup auth-config validation; namespace helper has no content reader.
- scripts/item6/pipeline_executor.py lines69/148 import removed compute_meter.
  This is private test-only coordinator, not actual production ChildGeneratorEngine.
  Research equivalent exclusion/compute semantics before changing, never replace
  with an ungrounded no-op merely to pass a test.
- tests/test_model_routes.py private provenance assertions expect provider data
  deliberately excluded from transcript; preserve pure segmentation tests and
  verify customer privacy, retain internal account-capability diagnostic checks
  using PG http-session/debug-http docs, not host files.
- Full build, all acceptance and final diff review still pending. CI unavailable,
  local gate only. Push WIP after every meaningful unit, noPR until full gate.

Unit34: model-route tests now assert customer transcript exclusion of private
model headers while existing pure per-segment and switch accounting assertions
remain. Account-capability execution detail uses retained http-session and
raw-debug documents, not a host directory. Combined model/cutover69 passed
(one expensive blocked-advance test excluded here, previously green in56).
Owner real-entry acceptance re-run with its required source and100s test budget:
1 passed17.43s. Earlier15s timeout was below its actual duration, not a defect.

Unit35: operator ZIP/health/shell fixtures now use real signed identity, separate
admin mapping and the actual logs.read database registry. Customer is403, admin
ZIP includes one bounded text file, shell remains data-free:27 passed.
pg_task_app gained an optional admin_subjects fixture argument only. No production
operator boundary changed or bypass added.

### Rebuild unit 36: phase display and dispatch fixture parity
- Signed PG HTTP phase-output projections retain previous/current/invalid/redo assertions. No unsigned compatibility middleware.
- Dispatch lifecycle fixtures install a test engine that uses real execute_job/claims; lost claim still cannot launch. Retired mode flag cannot disable an admitted start.
- Local bounded gate: 30 passed. Broad 40-file group: 219 passed. A combined Postgres group exceeded the outer tool budget after 81 dots, so it is not a passing result; split below before final gate.

### Rebuild unit 37: private attended graph compute contract
- Replaced deleted delivery compute-meter import with current requi_child_contract.compute.excluded_wait, using unique exact input/review identities.
- Real full-graph fixture now captures measured_work events and replays them into production ChildComputeEvents: two attended waits are excluded exactly (3 ms work from 5 ms elapsed). Not a no-op replacement; paid same-host producers remain refused by existing guards.
- Source-grounded local gate: private real graph plus child compute evidence, 9 passed. Unit 36 bounded signed phase/claim gate: 30 passed.
- Full gate still incomplete. Combined process budgets are not passes. Per-file sweep through preview/production is all green so far; remaining progress/provider tail not yet collected.

### Rebuild unit 38: progress and operator quality fixtures
- Progress parity consumes registered diagnostic child streams with current owned PG output documents, preserving exact gate/progress assertions. Missing artifacts still fail without a gate.
- Customer credits shell is signed; unmounted payment mutation fence remains 404.
- Quality config checks use a signed administrator and real debug.read registry capability, with row cleanup. The operator fixture now names logs.read and debug.read explicitly, not a bypass.
- Local gate: 14 passed. Broad next group stopped at six stale fixture failures after 76 passes; those six now fixed, remaining files still pending.

### Rebuild unit 39: private route fence with real signed customer
- Private revision assets/install routes still return 404 to a valid signed customer, not merely an anonymous auth refusal. Source-import fence unchanged.
- Local operator/quality/route gate: 32 passed. Broader 47-file rerun: 323 passed and this route fixture failure, now fixed. Next 40-file run exceeded outer budget with five unreported failures; not a gate pass.

### Rebuild unit 40: private simulation validation versus public fence
- Retained import/replay validation is tested through private function calls on real scoped PG, not retired unsigned/local HTTP exposure. Added signed-owner HTTP assertions for public simulation 503 in both routes.
- Local simulation gate: 11 passed. Browser-tail combined run timed out after 19 tests without a final summary; rerun with adequate browser budget before counting.

### Rebuild unit 41: no legacy session recovery or local rerun toggle
- Legacy event text cannot establish an engine session; retained explicit session still wins without duplicate reads.
- Rerun capability remains unavailable regardless of retired mode flag, including signed HTTP task detail, because new-generation checkpoint recovery is not implemented.
- Local gate: 21 passed. Final 41-file broad sweep reached 749 passed/1 skip/6 failures, including these three fixed fixture assertions plus three old UI expectations under review. Subsequent non-UI combined run has no completed result yet and is not a pass.

### Rebuild unit 42: restore generator open-gate continuation without legacy callback
- Confirmed defect: runner set continuation_phase but nothing consumed it. Removed dead callback wiring/variable.
- Added transactional owner-scoped reservation for an explicitly open, bypassed generator gate only after its exact worker is done, generation matches, no pending/running/paused job, and successor is pending. Preserves task model resolution and fresh job/provider admission. No interview or terminal continuation; stopped/closed/replayed policy refuses.
- Installed ChildPhaseLifecycle releases the prior serialized slot and registry before queueing/dispatching the next exact job. No global callback or automatic interview handoff.
- Local runtime gate (same-host, actual child, installed fake-provider all-phase, zero-credit HTTP, projections and lifecycle): 76 passed. Additional real PG lifecycle and runner regression group: 111 passed. Postgres store excluding 35 long graph variants: 50 passed. Full gate remains incomplete; actual installed multi-phase open-gate child acceptance is still required.
