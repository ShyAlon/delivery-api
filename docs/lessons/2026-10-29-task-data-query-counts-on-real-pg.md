# Count task data reads on the actual adapter

The task-read performance module still built a SQLite in-memory connection and
sent unsigned HTTP. Under the held Postgres-first bootstrap it had six failures:
four rejected requests and two cache reads missing tenant context. The other
11 cases passed. Fail-first measured 6 failed / 11 passed in 0.65s.

Replace the store factory with isolated app-role Postgres stores. Observe native
queries while letting the real adapter execute every statement. Seed current
and stale artifact rows in the disposable database. Signed HTTP uses production
tenant middleware, signature verification, and session validation. Direct cache
calls carry tenant scope. Keep all 17 original test cases and their behavior.

## Count semantics and assertion accounting

One cold / zero warm statements means task data queries. Signed HTTP also has a
live auth-session query on the cold request. Its separate production session
cache serves the warm request. Both counts are explicitly asserted. Transaction claim setup is not counted by this observer.
These tests do not claim one/zero total wire round trips. The write spy now
observes the real native statements rather than SQLite connection.execute.

Full records, phases/gates ordering, stale-artifact exclusion, bounded ascending
events, missing task, cache TTL/invalidation/copy/isolation, internal fresh reads,
and repeated/conflicting review retention assertions remain. The old adapter's
SQLite-specific query-count coverage is replaced, not retained. No production
code or cache-policy change; no timeout relaxation or skips.

Selected validation includes this module, native task projection and cloud API.
Hosted CI and the full suite remain unverified. The held default bootstrap is
not published by this receiver migration.

The existing task_read_cache.py owner-approval comment is unchanged. Recovered
owner agreement names session caching and stream refresh, not explicitly the
task-read TTL, so its authority is only partially evidenced. This migration
does not use that comment as permission and changes no cache policy.

Final measured selection: 55 passed in 4.40s on current bootstrap and 55 passed
in 4.72s on held PG-first bootstrap. The attempted new warm-auth assertion first
exposed the existing session-check cache (0 queries rather than 1); inspected
production SessionCheckCache and now assert that actual cold/warm behavior.
