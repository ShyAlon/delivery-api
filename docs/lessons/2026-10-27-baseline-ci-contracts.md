# 2026-10-27: Separate stale assertions from product regressions

A red test is evidence to investigate, not permission to relax it.

- A task GET synchronized a newly pending engine input after capturing events, then returned the old list. Keep the original expectation and refresh only after writing a new event. Repeated unchanged questions still do not write or re-read.
- Distribution lookup cannot assume top_level.txt or packages_distributions is complete. Match the imported file to installed RECORD ownership before reporting a version. Unknown modules still report unknown.
- Security tests must exercise the serving pool API and real auth-session claims. Revocation at the database is checked on the same backend's next transaction; the explicitly approved middleware cache has a separate 30-second bound. Test both contracts without sleeps.
- Build timestamps are behavior, not a Python source spelling. Listener flags remain exact assertions.
- Install the real sibling producer in the test interpreter for lineage tests. A checkout/export-directory variable does not make Python imports available.
- The complete browser smoke exposed SIGTERM hanging behind open SSE. Reproduce both SIGINT and SIGTERM in a real process, close streams before Uvicorn's wait, and chain/restore each signal's original handler. Never hide teardown failure by increasing a timeout.

Validation uses disposable local PostgreSQL and fake model runners only. No live database writes, paid model calls, merge, or deployment.
