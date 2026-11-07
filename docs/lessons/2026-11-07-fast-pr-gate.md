# 2026-11-07 - Gate every PR on a five-minute fast core; full matrix on demand

Every pull_request event ran the four-shard matrix plus browser smoke: about
25-31 billed minutes per run, 1,490 of 1,704 minutes on 2026-11-02. Most of
that depth is Postgres and browser coverage that does not need to gate a push.

The every-PR gate is now a single runner: boundary checks plus a deterministic
fast subset chosen by scripts/ci/classify_tests.py (content tokens for the
disposable-PG harness and Playwright; 264 files, about 2,300 tests, measured
1m46s locally). Every fast file already ran in the old PR shards, so the gate
is a strict subset executed in one process without the browser download. The
matrix keeps full depth on workflow_dispatch, nightly (cron 41 2 * * *), and
ready_for_review; image publication still needs the matrix.

Misclassification degrades safely: a Postgres test in the fast set just boots
the throwaway server (slower, still correct); a browser test would fail loudly
on the missing browser; a fast test sent to the matrix only loses PR coverage.

check-process-control.py is stale on main: its app.py markers predate the
current source and it is not enforced anywhere. Left out of the gate; fixing
its markers is a separate change.
