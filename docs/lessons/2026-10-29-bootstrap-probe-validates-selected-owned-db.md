# Bootstrap isolation probe must validate the selected owned database

The subprocess probe inherited the outer test DSN, which the held PG-first
bootstrap refused as external. Fail-first on latest baseline: 1 failed in 1.10s.
Its existing local path assertions alone also said nothing about the active
Postgres connection.

Drop only the inherited test DSN so nested pytest owns its disposable server.
Keep the poisoned Turso/real SQLite input and every original scratch-path
assertion. Add active-profile checks: PG DSN host/database exactly match the
owned running harness, selected role is delivery_app, and the live connection
reports that role and database. Current SQLite profile additionally asserts its
selected URL/path. No original assertion removed or weakened; no coverage loss.
Bound the child process and include stderr in failure diagnostics.

Current module: 1 passed in 2.19s. Held PG-first: 1 passed in 2.83s.
No production edits, skips, external database access or default-bootstrap flip.
Hosted CI and full suite remain unverified.
