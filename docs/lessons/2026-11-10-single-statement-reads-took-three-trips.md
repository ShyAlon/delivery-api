# 2026-11-10: single-statement reads took three network trips

## What happened
Authority and state reads (stop state, worker authority, task, session binding, engine session, progress cursor, sign-in liveness) each ran one SELECT but paid BEGIN, the statement and COMMIT as separate trips. 75 transactions per answer meant most of the 346 syncs.

## Cause
`_transactional` opened a transaction per method even when the method ran exactly one read.

## Change
- `ScopedPostgres.read_one`: BEGIN READ ONLY, identity, the statement and COMMIT leave as one pipelined batch. It refuses non-SELECT text and FOR UPDATE/SHARE, and retries a dead reused connection like `_begin`.
- Seven named reads use it when no connection or request transaction exists. A second statement inside one raises.
- `worker_authority` no-task branch reads the sign-in flag separately.
- Reads only. Writes are excluded: a retry after a lost answer is ambiguous.

## Not verified
Behaviour against Supabase's transaction-mode pooler with pipelined BEGIN/COMMIT has not been run. Sandbox proxy (80 ms each way): current shape 564 ms, fully pipelined 241 ms.

## Rule
A one-statement read must cost one trip. Authority is still read every time, never cached.
