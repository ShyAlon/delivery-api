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
