# Retire SQLite/Turso mechanism-only tests

Owner decision5 authorizes test retirement, not production removal.

Deleted15test functions:6Turso cases in test_tasks.py (local writes/sync, sync failure, offline startup fallback, tuple rows, SSE tuple rows, plain SQLite fallback);4shared-lock functions across test_store_read_lock.py and test_sqlite_task_read_lock.py;5file/schema cases (final-verification gate backfill, additive binding columns, validation phase backfill, stale PRAGMA columns, selected-parent creation). The4lock functions expand to28collected cases, so these15functions represent39cases. No remaining imports of deleted modules found.

Replaced2local factory/default-path tests with PG factory reopen/persistence through a restricted scoped workspace and PG runtime config assertions under poisoned SQLite/Turso constructors and sqlite3.connect. The factory takes URL syntax, not libpq keyword conninfo; the fixture builds a PostgreSQL URL from its disposable DSN. Production factory still supports legacy choices; this is PG-only test coverage, not a claim those choices were removed.

Coverage lost:legacy adapter sync/fallback/tuple mapping, SQLite shared connection cursor serialization, SQLite additive schema/backfill handling and local file creation. Existing PG concurrency/RLS/transaction tests remain. Read-only config import tests remain, including local inputs; global test bootstrap is a later theme. No production files changed, no surviving assertion weakened. SQLite query-budget/cache reference tests and importer tests remain untouched.

Measured validation:
- Before deletion:41passed in1.88s, consisting of39removed cases and2replaced cases.
- One-off absence check red before deletion and green after.
- New PG replacements:2passed in1.63s.
- Final tasks/schema binding/validation/isolation/config/Postgres store/bootstrap/independence slice:167passed16skipped in24.39s. Skips are retained real-producer revision-stage cases, not removed or newly skipped by this PR.
- Enabling producer source first revealed missing langgraph:16failed167passed in27.12s;untouched base reproduces16failed49passed in7.91s for Postgres store. Enabling producer dependencies as well:16failed49passed in13.80s. All16 fail at revision_stage_writer.py:115 because hashlib.file_digest is unavailable on Python3.10;untouched base with full dependencies reproduces16failed49passed in13.64s. pyproject declares Python>=3.10 while Docker/CI use3.12. This pre-existing compatibility gap requires separate approval for production repair;no full-suite/hosted CI pass claimed.

Lesson: delete backend mechanisms without pretending their PG equivalents use SQLite locking/schema internals. Retain actual PG transaction and tenant isolation proofs, and distinguish retired contracts from still-existing production compatibility code.
