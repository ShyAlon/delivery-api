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
