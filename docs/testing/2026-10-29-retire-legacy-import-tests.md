# Retire the seven legacy-import execution tests

Owner decision6 says old SQLite data need not remain importable and approves deleting the seven importer tests. This PR removes tests/test_legacy_import.py and its private SQLite source/checkpoint fixture. Production importer, planner and entrypoints remain unchanged pending separate approval. No source data or live database touched; baseline only used the disposable test database and temporary files.

Coverage lost:successful task/checkpoint import and coherent source copy/backup, rollback before commit including published-file cleanup, changed mapping refusal before backup, nonempty target refusal, source-edit-after-review refusal, read-only receipt reconciliation, concurrent source-change rollback. These were the only direct tests of apply_legacy_import and reconcile_import found in tests. Production legacy_import.py therefore loses its direct execution/reconciliation coverage.

The separate test_legacy_ownership.py stays:four planner/security functions (seven parameterized cases) cover quarantine without mapping, explicit mapping/digest, invalid/unbound ownership and escaped files. They still use a SQLite fixture and are outside the approved enumerated seven importer tests. Evidence parity's SQLite half and query-budget/cache references also remain; this is not a claim that all SQLite test consumers are gone.

Validation:
- Before deletion:7passed in1.10s.
- One-off absence assertion red before deletion, green after;no deleted-module imports or remaining apply/reconcile test references found.
- Retained ownership/evidence parity/PG bootstrap/native task projection/review feedback/phase gate slice:27passed in2.69s, no skips.
- No surviving assertion changed or weakened. Only tests and this dated lesson changed. Full suite and hosted CI unverified. Previously reported Python3.10 file_digest compatibility gap in producer revision-stage cases remains unresolved, outside this slice and PR.

Lesson: retirement permission for an explicit test set does not authorize removing adjacent ownership/security contracts or production import code. Report the direct coverage hole clearly so later production retirement cannot be mistaken for tested support.
