# 2026-10-27 - Tests that pin incidental numbers go stale silently

Two main-branch reds were stale expectations from our own recent merges, not
product bugs: model_routes pinned availability to execution-open alone until
the availability fix made the billing key part of it, and the everything-ZIP
browser test pinned the exact "6 of 7" section index until the Request Timing
section shifted it. Pin the NAME and the honest-count relationship, never an
ordinal. A test that reuses another module's fixtures must reuse its skipif
too (decision_load_tracing crashed on a None source path instead of skipping).
test_engine_session_authority is a different animal: the engine removed
tenant_worker.owner_scoped_dsn in 878ba4c and these four tests still call it -
cross-repo API drift, red on main, needs the tests moved to the current
pg_session_store(pool, owner, session_id) shape with engine sources present.
