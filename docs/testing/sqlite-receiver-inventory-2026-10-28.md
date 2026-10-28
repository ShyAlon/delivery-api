# Remaining SQLiteStore receiver inventory (2026-10-28)

Source inspection at main857a29d before A32. Exact token
SQLiteStore in tests/**/*.py:41 files. 33 files contain constructor syntax,87
occurrences (A32 removes two,leaving85). Including sqlite:/// literals:51 files. Constructor syntax includes
intentional forbidden-open assertions,so it is not a count of live receivers.

Primary buckets are exclusive for counting;notes retain mixed responsibilities.
No behavior is authorized for deletion by this inventory. In particular retired
contracts require translation/reconciliation in B/C/D,not skipped tests.

Primary counts:guard=16,migrate=14,reference=5,retired-contract=6.

| File | Primary bucket | Receiver disposition |
|---|---|---|
| tests/conftest.py | reference | SQLite/PG evidence parity reference receiver;global sqlite:/// bootstrap also needs A migration. |
| tests/pg_runtime.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/pg_workspace.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_admin_stop_run.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_api.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_auth_quota.py | migrate | Mixed: C retires local/OAuth/basic sessions; preserve quota/limiter/security behavior in PG cloud contracts. |
| tests/test_config_readonly.py | retired-contract | Selected SQLite path/file creation is retired backend behavior; keep config-import no-write invariant under PG. |
| tests/test_decision_pages.py | migrate | All current callers pass PG; remove unused optional SQLite fallback. Constructor call in poison test is a guard. |
| tests/test_event_driven_progress.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_gate_revision.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_http_cutover.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_interrupted_replay.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_legacy_import.py | reference | Deliberate legacy SQLite source receiver for import compatibility;separate D data-retirement proof required. |
| tests/test_model_routes.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_operator_capacity.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_operator_failure_diagnostics.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_package_integration.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_pg_native_event_progress.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_pg_native_phase_gate.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_pg_native_review_feedback.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_pg_native_run_usage.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_pg_native_task_projection.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_pg_store_independent.py | reference | Reference class identity/MRO assertion,not SQLite execution. Replace with no-module-dependency proof at D. |
| tests/test_phase_output.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_phase_output_legacy_rerun.py | reference | Legacy rerun reference contract needs explicit immutable-generation policy reconciliation;parked,not silently deleted. |
| tests/test_phase_start.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_progress_protocol.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_schema_binding.py | migrate | Migrate binding persistence; SQLite reopen/add-column case is retired contract. |
| tests/test_session_refresh.py | retired-contract | Local OAuth session-handle rotation contract retires in C;preserve cloud expiry/revocation/CSRF coverage. |
| tests/test_simulation_validation.py | guard | Poison/no-parent-call assertion only;no live SQLite receiver. |
| tests/test_spa_shell.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_spa_stale_mount.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_sqlite_task_read_lock.py | retired-contract | SQLite shared connection interleaving mechanism;not a PG receiver contract. |
| tests/test_store_read_lock.py | retired-contract | SQLite shared-connection/cursor locking mechanism;not a PG receiver contract. |
| tests/test_task_diagnostics.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |
| tests/test_task_read_statements.py | reference | SQLite reference query-budget fixture; preserve PG projection performance contract before D deletes reference. |
| tests/test_tasks.py | migrate | Migrate shared API/bootstrap and simulation; factory/Turso/local schema cases are retired contracts. Legacy transcript failure remains parked. |
| tests/test_test_isolation.py | retired-contract | SQLite PRAGMA/add-column migration mechanics retire with SQLite. |
| tests/test_ui_playwright.py | migrate | Migrate shared subprocess shell and user/session seed; local auth behavior belongs to C. |
| tests/test_validation_panel.py | retired-contract | Only SQLite receiver is legacy SQLite phase backfill:retired contract. Remaining panel functions are backend-free. |
| tests/test_waiting_input_dedupe.py | migrate | Actual SQLite-backed application fixture receiver must move to PG. |

## A forecast

The14 primary migrate files include the large mixed HTTP cutover,task/API,shared
UI and auth suites,not14 equal units. Estimated remaining A themes:12-20 PRs
at current small-theme size. This is a guess,not a schedule. Shared global
bootstrap can expose order coupling after individual ports;reference and retired
contracts remain explicit B/C/D responsibilities. A32 does not remove a file
from this inventory because test_tasks.py retains other SQLite receivers.

## Refreshed A status at main d444f57 (after A58)

Measured35 literal SQLiteStore files:4 migrate,19 guard-only,5 reference,
7 retired-contract. Literal migrate files:auth_quota,package_integration,tasks,
ui_playwright. API is an implicit legacy receiver (four local link/budget cases)
without token:including it36tracked/5migrate. Global conftest is primarily
reference but still owns global SQLite bootstrap migration. No token-file
reduction during browser ports,so token count alone hides the work.

Shared browser module:55 PG functions/60 expanded cases;72 legacy page/ui
functions/77expanded cases;three additional standalone legacy auth/bootstrap
browser functions. Two separate SPA browser modules already PG. Planning
classification of72remaining:task/layout/completion16,operator/debug/audit14,
gate/rerun12,runtime/refresh11,decision/state9,start/model-route5,
interview/composer5. Categories are a planning judgment,counts measured by AST.

Remaining non-browser work:producer package integration,global bootstrap,
task legacy/factory reconciliation,auth quota/security separation,and four API
local contracts. Some boundaries belong to C,not bypassing PG owner guards.
Guess,not schedule:10-16 more A PRs at current small-theme size,roughly8-12
browser themes+2-4 other closures. B1-2,C2-4,D1-2 PRs are fresh cautious guesses,
not measured estimates or permission to widen scope. Metrics/checkpoint
retirement after D excluded. A59 below migrates the five start/model-route
functions,leaving67shared legacy functions/72expanded cases and60PGfunctions/
65expanded cases after that PR,assuming no other changes.
