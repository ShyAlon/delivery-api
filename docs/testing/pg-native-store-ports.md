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
