# PG-owned task family, 2026-10-28

First 2B family: task_snapshot,rename_task,task_artifacts,_task_record,_gate_record,
_event_record are now Postgres-owned. Snapshot/artifact/rename statements use native
%s parameters via _execute_native under the existing transaction;write invalidation
is preserved, and PG UniqueViolation maps directly to the existing title error.
One statement still returns the bounded newest events plus task/artifact manifest.

The failing test rejects these task queries through _Connection and poisons SQLite
parent methods, so a forwarding-only port cannot pass. Collision and audit-failure
rollback,missing tasks,parsed gate options and artifact reads remain assertions.
Production callers include task_get (_task_detail_snapshot),rename_task HTTP route,
artifact projection readers and internal task-record projections. The existing
manifest/read-scope/query-count/browser suites run alongside the new regression.

Not removed:SQLiteStore base,remaining inherited methods,dynamic wrapping,other
SQLite SQL/error/row/lock adapters,runtime SQLite backend and reference tests.
No data migration,deploy/restart,paid calls or phase5 activation.

Environment recovery caveat:the prior local test environment disappeared. Fresh
unbounded dev install chose pytest9.2.0,which exposed Starlette State monkeypatch
fixture teardown incompatibility (app.state.store lost after first test). Pinning
only framework versions did not fix it;pytest8.4.2 did. Local validation currently
uses Python3.12.15,pytest8.4.2,FastAPI0.115.6,Starlette0.41.3. Clean latest-dev CI is
not claimed green;fix that test-fixture compatibility separately,do not suppress it.

Validation:81focused passed,0skips9.54s. Existing two-test fast-scope sample:
SQLite cold5.518s/warm3.628s;PG cold3.187s/warm1.873s,all passed. Same scope
caveats as postgres-workspaces.md;not an equal-work speedup or fullsuite/deploy
claim. Hosted CI and deployed topology remain unverified.

## Events/progress/audit family

PG owns add_event,events,progress_cursor,ingest_progress,audit,audit_entries,
waiting-input lookup,diagnostic block validation and bounded failure diagnostics.
Native RETURNING id replaces lastrowid;named cursor fields replace positional rows.
Existing tenant/generation diagnostics write now uses native SQL too. PG jsonb
blocks stay dictionaries;created_at is exposed under the existing ts read shape.

A poisoned-parent/bridge regression fails before the port. A concurrent replay
regression also exposed/fixed a pre-existing PG race with task-row serialization.
A fault after event/seen insertion proves cursor/event rollback and retry success.
No schema/migration or customer/operator grant change. Existing transaction timing,
cache invalidation,event notifications and outer savepoint ownership remain.

Focused API/browser/read-scope set48passed0skips8.56s. Restored crossrepo source
sweep is reported separately;no unavailable-source skip is called full acceptance.
Evidence fast scope (same3contracts):SQLite cold1.319s/warm0.269s;PG
cold1.270s/warm0.215s,all green. Single local samples,not fullsuite/deploy proof.

Old cloud fixture still uses dynamic State setattr and fails on pytest9 between
route tests;new pg_workspace fixture is fixed by328. This separate test-only
compatibility debt is not suppressed or mixed into the runtime port.
Restored engine/spec sources:81passed plus one missing bundled-Chromium failure
(no source skips);after installing the required bundled browser,the exact failed
revision/ask_changes case passed3.91s. Full combined rerun pending. Two old cloud
route tests intentionally excluded here due to separately reported fixture debt.

## Review/draft/feedback family

PG owns11methods:comment add/read,review retain/read,draft save/read,feedback
list/upsert/private row/update/delete. Native%s,RETURNING,named payload columns and
ON CONFLICT DO NOTHING replace SQLite adaptation. Existing guards/owner locks stay.
Feedback reads its engine session through native tasks query. Same tenant/actor
checks and atomic audit ownership. Concurrent conflicting review pin keeps one
payload and raises conflict for the other;failed feedback audit rolls back insert.

60focused tests passed0skips10.85s,one stale rerun contract test excluded because
its explicit correction is in separate332. Evidence cold/warm:SQLite1.624s/0.321s,
PG1.494s/0.269s,all green. Single samples,not fullsuite/deploy claims. Hosted CI and
deployed topology unverified. No schema/data/activation changes.

## Phase/gate/rerun/session family

PG now owns simulation config,gate toggle/wait/decide,phase update,completed-interview
reconciliation,next/retry claims,model route/default,engine session read,immutable
interview schema binding/read,and rerun prerequisite. begin_interview_start uses a
PG-owned CAS implementation instead of super(),then the existing PG binding write.
Native placeholders/RETURNING/savepoint ownership;all predecessor/follower/state
checks,gate revisions,scope/owner guard and cloud rerun refusal remain.

Task-row locks serialize read/check/write for gates,phase changes,claims,
reconciliation and schema binding. Real concurrent next-phase red test now proves
oneTrue/oneFalse,one audit. A poisoned parent/family-bridge test proves independent
SQL. Gate audit fault proves state+revision rollback. No paid engine invoked.

163combined source-enabled tests passed0skips84.49s on latest environment.
Includes all prior native,store/revision,cloud/operator,API/browser,evidence,
request/query-count suites. Hosted CI/deployed topology unverified.
No schema/data migration/destruction,activation,deploy,restart or spend.
Evidence fast-scope single sample:SQLite cold1.532s/warm0.285s;PG
cold1.573s/warm0.264s,all passed. Same3contracts,scope/setup caveats retained;
no fullsuite/deploy speed claim. Missing-task/phase return/error shapes retained.

## Remaining run/usage/artifact family

PG owns link/deactivate,state/run/submission,total_spend,run artifact add/read,
task artifact reference add,quota_allows and add_user_usage. Existing PG quota
reader uses native SQL too. PG usage explicitly rejects a different owner before
write;RLS still protects run+usage rows. Legacy cloud execution route blocks and
local-auth refusal stay. Runtime callers include status/artifact routes,health
spend,job budget state and existing add_usage/attribute_run PG overrides.

Poisoned parent/family bridge test red first;now real run accounting,artifact,
quota,submission/state and cross-owner isolation pass.164source-enabled combined
tests passed0skips86.43s. Hosted CI/topology unverified. No runtime backend removal,
base removal,data migration/destruction,deploy/restart,activation or spend.
Evidence fast-scope sample:SQLite cold1.588s/warm0.272s;PG1.662s/0.392s,
all green. Same3contracts/single sample,not fullsuite/deploy speed proof.
Static closure after this port:only SQLite schema/init commit helper methods and
10runtime-refused local-auth methods remain inherited;all4query constants have
PG overrides. Helper access/dynamic wrapping closure must be tested before removal.

## Base and transaction registry closure

PG no longer inherits/imports SQLiteStore. Its PHASES tuple comes directly from
neutral canonical phase definitions. Removed unused no-op _lock compatibility.
All4projection constants and everyself method/helper reference are owned. The only
formerly inherited names left were unused SQLite schema/commit/session-key helpers
and10local-auth names now explicitly installed as scoped unsupported operations.

PG_OPERATIONS explicitly enumerates133public operation declarations;coverage proves
actual public callables match that registry plus10auth refusals,and every callable
is transaction-wrapped. Storage protocol public methods remain available. AST
proof rejects unresolved self references or super() delegation in PostgresStore.
This is static consumer closure plus runtime focused coverage,not full dynamic
program reachability. Runtime SQLite backend,its tests,module API and data remain.

Bridge/_Row/_Cursor/sqlite3 exception conversion remain because PG-owned methods
still consume them. Base deletion is not evidence that those adapters are unused.
No premature removal.167source-enabled combined tests passed0skips82.54s.
Hosted CI/deployed topology unverified. No schema/data/backend deletion or activation.
Evidence same3contract single sample:SQLite cold1.477s/warm0.215s;PG
1.595s/0.248s,all passed. Scope/setup caveats retained,no fullsuite/deploy claim.

## SQLite row/cursor/exception adapters: last-use proof (2026-10-28)

After the PG base removal, the remaining PG operations and prepaid/credit helpers still use `_db.execute` with question-mark SQL. That placeholder/dialect bridge is retained, not declared unused. The row/cursor and SQLite exception adapters are a separate, proven dead layer:

- Native event/audit/review/diagnostic ports already own RETURNING. Production search found no non-SQLite lastrowid consumers; an AST guard covers every delivery production module except the deliberately retained SQLite implementations.
- Raw psycopg dict rows and cursors replace `_Row`/`_Cursor`. The same 169-test slice passes without positional-row compatibility, implicit RETURNING, or SQLite exception translation (0 skipped).
- create_task was the final exception-conversion/executemany consumer. It now uses native SQL, native UniqueViolation and explicit phase/gate writes. Collision tests preserve eight phases/eight gates, case-insensitive error shape, rollback, and a subsequent successful create.
- No `_db.commit`, `_db.rollback`, or `_db.executemany` consumers remain outside SQLite; AST guards retain that proof. The scoped transaction owner remains unchanged.
- Failing-first proof: adapter-removal contract failed while native collision behavior passed. After removal both passed, followed by the acceptance slice. This is scoped static/runtime evidence, not a claim about arbitrary future dynamic code.

The remaining query dialect bridge cannot yet be deleted: revision installation, worker jobs, expense/credit helpers, artifacts and external prepaid/credit helpers still consume it. Runtime SQLite/auth/backend removal and deployed topology remain later work. Hosted CI is unverified.

Final slice with AST production guard: 170 passed, 0 skipped in 88.81s. Same three evidence contracts per backend, cold/warm local samples: SQLite 1.586/0.404s; PG 1.727/0.303s. These are single-run scoped timings, not full-suite or deploy predictions.

## Native worker query consumers (2026-10-28)

Nine worker operations now own eleven PG-native statements: enqueue, recoverable listing, task diagnostics, lookup, claim, interrupted pause, explicit reauthorization, stopped completion and ordinary completion. Writes use native cache invalidation; transaction wrappers, RLS, capability checks, claim CAS and session binding are unchanged.

Failing first: the real worker lifecycle failed on enqueue with the worker-table query bridge blocked. After porting, that same fence permits concurrency (four contenders, one winner), pause/reclaim, failure, new live-session explicit reauthorization, cross-owner invisibility/refusal, idempotent completion, and stopped-worker quiescence checks. A second test injects failure after a real native INSERT and proves no durable job survives. Scope caveat: lifecycle stop itself and the common task projection are separate bridge consumers; the test explicitly exempts setup/stop and that task projection, rather than claiming the whole transitive graph is bridge-free.

Source-enabled combined slice: 172 passed, 0 skipped in 93.41s. Same three evidence contracts, local cold/warm single samples: SQLite 1.606/0.296s, PG 1.610/0.286s. Not full-suite or deploy predictions. Hosted CI and deployed topology remain unverified. Revision/artifact/credit/external helper and task-lifecycle query consumers still require later ports; the bridge stays until their last use is proven gone.

## Native artifact/manifest/producer consumers (2026-10-28)

Twelve query-bearing artifact/manifest/producer methods (including private readiness CAS) now own 23 native statements. Publication write invalidations are explicit. Existing transaction boundaries, exact validation-report hash binding, content-addressed immutable bytes, readiness/member hash check, supersession and producer markers are unchanged.

Failing first: a real manifest publication with artifact/producer-table bridge calls blocked fails before port, then passes unchanged. The fence exempts the still-unported common task projection only. It covers manifest readiness/snapshot/revision, hash-verified bytes, durable producer state and published CAS, foreign-owner invisibility and byte refusal. A readiness failure injected after real artifact writes leaves the exact previous snapshot and no partial manifest member. Successful replacement omits/supersedes the old member while keeping its immutable bytes readable by hash.

Expanded source-enabled acceptance including manifest reads and publication recovery: 201 passed, 0 skipped in 86.50s. Same three evidence contracts cold/warm single local samples: SQLite 1.718/0.284s; PG 1.703/0.272s. Not full-suite/deploy predictions. Hosted CI/deployed topology unverified; no flags activated. Revision installation, task lifecycle, credit and external helper consumers remain before query-bridge retirement.

## Native task lifecycle/projection/session queries (2026-10-28)

Nine methods now own 23 native statement sites (including fixed-table DELETE loop): auth-session probe, task/list projections, request/confirm stop, tombstone, delete, engine-session binding and binding read. Writes retain explicit invalidation. No stop/accounting authority changes. Installed-execution anchor is left with revision-journal work; create_link is a remaining run-family query consumer, not hidden as completed.

Failing-first: real lifecycle query fence and injected post-delete failure tests both fail before port. After port they preserve idempotent generation fencing, quiescence requirements, running-worker refusal, stop pending delete refusal, durable stopped tombstones, retained jobs/session binding, ordinary hard delete, immutable session binding, owner isolation and rollback after actual DELETE. Existing worker/artifact tests now traverse native task projections too.

Same three evidence contracts local cold/warm samples: SQLite 1.564/0.263s; PG 1.544/0.281s. Local scoped samples, not deploy predictions. Hosted CI/deployed topology remain unverified; no deploy/restart/live database/spend/destruction/phase5 activation.

### Expanded ledger acceptance debt discovered (not waived)

Adding test_attempt_ledger.py exposed order-dependent confirmed-expense tests: test_confirmed_guard_ignores_holds_and_inflight_calls_finish expects two alert episodes but finds one; test_confirmed_guard_platform_counts_other_owner_and_rolls_off expects platform denial but gets allowed. Both pass isolated on main and current. Same-order expanded comparison on fresh main: 274 passed/2 failed; current including two new lifecycle tests: 276 passed/2 failed. Both sources enabled: zero skips. Exact cause remains unresolved, no assertions changed, no green expanded-suite claim. Earlier seven engine-source skips are exercised and pass once DELIVERY_TEST_ENGINE_SOURCE is set. Track expense-guard isolation/order investigation separately.

Task/store/cloud/operator/API/browser/manifest acceptance without the separately disclosed expanded ledger suite: 203 passed, 0 skipped in 85.70s (both sources enabled).

## Native revision-journal query consumers (2026-10-28)

Nineteen methods own 79 native SQL sites covering held restart/lineage/preparation, source review/staging, private acceptance/revocation/install journal, promoted evidence and installed-anchor reads. The final INSERT OR IGNORE consumer (acceptance revocation) now uses explicit ON CONFLICT DO NOTHING. No policy, evidence schema, phase5 flag, activation availability or transaction boundary changed. This port does not authorize or activate any runtime installation.

Failing-first static ownership and real held-lineage/journal bridge fence tests both fail before port, pass after. Static proof covers all nineteen method bodies; runtime fence covers held restart/lineage/prepared/staged, replay/CAS rejection, no task/session mutation and absent installed anchor. Existing real staged/private installation tests in test_postgres_store.py remain exercised; static coverage alone is not claimed full behavioral coverage of every branch. RLS, audit rollback and journal digest checks unchanged.

Source-enabled combined acceptance: 205 passed, 0 skipped in 86.60s. Same three evidence contracts cold/warm single local samples: SQLite 1.465/0.254s; PG 1.474/0.246s. Hosted CI/deployed topology unverified. Previously documented expanded expense-guard order dependence is unresolved and not part of this green scoped claim. Remaining consumers include scheduling command locks, provider/expense/credit helpers, operator snapshot and create_link.

## Native provider/expense/prepaid SQL (2026-10-28)

Ten PG methods (16 sites), attempt_ledger (19 sites) and prepaid (7 sites) now use native PG queries. Credit audit attribution explicitly requests PG markers. Writes retain invalidation; the confirmed-expense stored function is marked write=True because denial can persist an episode/outbox. Owner/global advisory locks, exact usage/replay checks, unresolved holds, positive-balance admission, exact one-time debit and raw evidence are unchanged. Retail-compute and credit-history helpers remain a separate theme.

Failing-first all-family static guard plus real provider/prepaid/outbox bridge fence pass after port. Runtime test proves one exact debit under replay, overdraw then next-call refusal, refused-start rollback to reserved, raw usage, outbox dedupe/claim/retry/uncertain recovery and foreign-owner refusal. Ledger/expense-alert/credit-ledger acceptance, both producer source trees enabled: 87 passed, zero skips in 7.43s. Scoped cumulative acceptance: 217 passed, zero skips in 91.13s. These are separate invocations, not a claim that the earlier order-dependent combined ledger failures are fixed. That debt remains recorded and unresolved.

Same three evidence contracts local cold/warm samples: SQLite 1.555/0.264s; PG 1.593/0.252s. Hosted CI/deployed topology unverified, no live spend or flags activated.

## Native retail-compute/account helper consumers (2026-10-28)

Retail ledger's 21 query sites use native execution/write invalidation and explicit PG credit-audit attribution. retail_compute_status is native. Credit balance and account ledger retain legacy '?' default for existing callers/reference tests, while PG explicitly supplies '%s' and a minimal executor facade using _execute_native. Credit history has no placeholders and uses the same native facade. This facade does no SQL translation; query timing/transaction scope remain owned by the native executor. Operator migration queries are unchanged and never invoked automatically.

Failing-first real query bridge fence covers retail contributions, compute segments, account balances/history and customer ledger. Exact fractional remainder, replay conflict, compute settlement, rollups and foreign-owner refusal pass. Family suite: 74 passed/2 failed/0 skipped. Newly exposed static UI expectations test_session_cost_text_is_untouched_and_balance_is_wired and test_header_uses_the_tooltip_and_debug_has_the_history fail identically after restoring all changed production files to main. They target unchanged frontend wiring. No assertions changed or debt declared fixed.

Cumulative scoped acceptance: 218 passed, zero skips in 92.30s. Both producer sources enabled. Same three evidence contracts local cold/warm samples: SQLite 1.634/0.281s; PG 1.698/0.253s. Prior combined-order expense debt remains unresolved; no full-suite green claim. Hosted CI/deployed topology unverified.
