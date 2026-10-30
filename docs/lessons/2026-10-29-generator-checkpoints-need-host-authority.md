# Generator checkpoints need host authority, not a file path

The server runs the CLI and optional isolated child, not only run_fresh_graph.
A worker_graph-only saver replacement leaves the normal route on SQLite and
breaks full-feature parity. The factory belongs to a bound JobRuntime and must
capture original worker attempt at bind, not silently adopt a retry later.

A private staged revision uses a future logical execution identity before
installation. That is not an engine_sessions row and must not become one merely
to satisfy checkpoint RLS. Keep private namespaces under the owning source row,
use hash-bound immutable snapshots for review/fork, and promote only under the
existing authenticated acceptance + generation CAS. Never widen checkpoint RLS.

Snapshot/fork must preserve ancestry, versions, scheduled nodes, pending writes
and task_path, not only channel_values. A simple counter graph roundtrip does
not prove revision continuation semantics. Test real graphs, an interrupted
schedule, pending task writes, source isolation and an empty-target refusal.

Locks order DB task/job first, then saver mutex. Snapshot and saver share the
same scoped transaction instead of borrowing a second pooled connection while
holding the task lock. The second checkout can deadlock even below pool cap.

No history import/fallback is needed. Existing tests constructing SQLite-only
revision sources need a PG replacement fixture, retaining their safety
assertions. Do not reintroduce production SQLite import to make tests pass.
