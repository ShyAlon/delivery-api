# Hidden tasks retain evidence, not display-name ownership

Delete fences future work and hides the task, preserving accounting/artifacts. The original all-row owner/lower(title) unique index kept names reserved after the list hid the row. Replace only this index with a unique partial index WHERE deleted_at IS NULL. Keep case-insensitive active uniqueness and owner isolation in PostgreSQL, not a race-prone Python precheck. A stop request without hide still reserves the name.

New reviewed migration drops/recreates one index transactionally, not source rows. Exact-file hash allows the existing typed owner APPLY workflow; unknown DROP remains refused. Readiness probes the predicate so an old same-named full index is not mistaken for the fixed index. Owner must review/apply before rebuilding; no live change by us. Never rename hidden history just to free a name.
