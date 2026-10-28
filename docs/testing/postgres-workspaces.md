# PostgreSQL fixture phase 1, 2026-10-28

This is additive test infrastructure. No runtime storage removal, data migration,
authentication bypass, or replacement/deletion of old assertions is included.

`pg_workspace_factory` uses the existing session-migrated disposable PG16 server.
It fails before connecting if the harness is external/off/missing/failed. Every
workspace gets fresh distinct owner/session UUIDs. All store operations use the
restricted delivery_app login, SET ROLE requi_backend and transaction claims.
The task app fixture uses production TenantMiddleware and SupabaseIdentityVerifier
with a locally generated ES256 test signing key (no remote auth/provider calls).
API/browser requests still check live auth.sessions and database RLS. The small
slice does not test JWKS fetching or the Supabase auth service.

Factory teardown deletes owned tasks through PostgresStore and revokes the seeded
session, including after a failing test. Owners and FK-linked audit history remain
until the disposable server is destroyed at process exit. This avoids broad admin
DELETEs and preserves audit semantics. A session-shared store/pool avoids per-test
migrations; distinct tenants prevent test order from becoming identity isolation.
The app fixture restores monkeypatched state. TestClient is closed without its
lifespan; Uvicorn uses lifespan=off so production engine startup is not invoked.
The browser fixture joins its server thread on exit. No paid calls are made.

## Fast-scope measurement

Run with the repository environment and installed Chrome, for example:

```
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome .venv/bin/python scripts/testing/measure-pg-slice.py
```

The script runs each scope in its own Python process, twice via pytest.main.
Cold includes initial imports and server start/migrations. Warm reuses that same
migrated server and Python modules, but makes new test fixtures, tenants and browser.
It saves logs and JSON under /tmp. Timing uses monotonic wall time around pytest,
not pytest's narrower test-only duration. Browser launch is included in both.

Local measurement on 2026-10-28 (one sample, seconds):

| Scope, two tests each | Cold | Warm | Exit codes |
| --- | ---: | ---: | --- |
| Existing SQLite API lifecycle + browser rename/delete | 5.468 | 3.637 | 0 / 0 |
| PG API lifecycle/isolation + browser list/filter | 3.606 | 1.937 | 0 / 0 |

These are scoped feasibility numbers, not an equivalent-work performance win and
not a full suite/deploy prediction. The old browser fixture seeds many states and
uses a subprocess server; the new browser slice covers fewer interactions with an
in-process server, but adds signed authentication and RLS. Both scopes currently
start PG because the global conftest already starts it, including SQLite tests.
No cache dropping was done. Hosted CI and deployed topology remain unverified.

A first warm measurement failed because repeated pg_harness.start relabeled its
own server external. A failing regression now proves owned-server start is
idempotent. External DSNs are still refused by this new factory.

## Phase 2A task-browser parity

`tests/test_postgres_task_browser.py` adds real browser task create, focused Enter,
textarea Ctrl+Enter, Escape cancellation, rename, reload, delete and missing-task
checks. Duplicate-title errors must retain error/cause/next and show the actionable
next step. The error dialog replaces the create form by current production design.
No production code or original SQLite assertion is changed.

Run the expanded timing with `--task-browser`. Local one-sample timings:
SQLite browser reference cold8.452s/warm7.250s; PG browser parity
cold7.655s/warm5.993s, all passed. The old fixture seeds many states and uses a
subprocess server; PG starts a fresh authenticated in-process server/browser per
test. These remain scoped feasibility measurements, not identical workload or a
full-suite speed claim. Cold includes harness setup; warm excludes initial migrations.
Combined PG+unchanged SQLite selected contracts:10passed,0skips,17.22s.

## Phase 2A progress/evidence contracts

`test_store_evidence_parity.py` runs identical contracts on both backends for:
- phase detail/count updates, atomic progress/event cursor replay, cursor-gap
  refusal and recovery, ordered event snapshot/incremental reads;
- review comments, immutable revision-pinned review evidence, draft revision
  binding and structured audit details;
- feedback upsert/update/delete, validation, actor checks and audit records.

Additional PG-only tests cover cross-owner evidence invisibility/write refusal,
immutable byte/manifest publication, reads after local source deletion, and session
revocation without bypassing running-task delete refusal. Intentional differences:
SQLite is shared/local and has no tenant RLS; PG publication uses complete immutable
manifest revisions rather than treating an arbitrary local file ref as authority.
The existing manifest regression suite runs alongside this slice. No new runtime
storage behavior or original assertion is changed.

Factory cleanup now respects production running/paused/accounted task deletion
refusals and revokes the auth session anyway. Those tasks and FK history remain
only until the disposable process server is destroyed. The fixture must not force
state to stopped, erase accounting, or weaken delete guards for cleanup convenience.
Unknown cleanup errors still fail loudly. A regression proves the running record
remains running and its auth session is revoked.

`--evidence` measures the same three parameterized contracts per backend, excluding
PG-only negative/publication extras in both scopes. Local cold/warm wall time:
SQLite1.722s/0.283s;PG1.691s/0.272s, all passed. Both cold runs include global PG
startup and factory construction. Separate harness-only sample:0.915s,43migrations.
Single samples, no significance claim;hosted CI/full-suite/deploy time unverified.

Pytest9 compatibility repair:patch app.state._state with monkeypatch.setitem,
not its dynamic attribute with setattr. Existing multi-fixture sequence reproduced
missing app.state.store before the fix;7workspace/browser tests passed afterward,
then16including evidence passed on pytest9.2.0/FastAPI0.162.0/Starlette1.10.0.
No runtime change or assertion weakening. Hosted CI remains unverified.
