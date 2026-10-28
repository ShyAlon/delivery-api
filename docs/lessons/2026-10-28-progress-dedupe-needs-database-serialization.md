# Progress dedupe needs database serialization, 2026-10-28

The PG operation inherited a SQLite read/check/insert sequence. With two separate
PG transactions, both can see no cursor and one then fails a unique constraint.
A real concurrent replay regression failed before the native port. Lock the owned
task row before reading the cursor, then commit event,seen key and cursor together.
Prove one True and one False,one event,and rollback/retry after a mid-write failure.

Native diagnostics reads must use PG created_at and decoded jsonb, not a SQLite ts
column or an assumed JSON string. Keep operator-only boundaries and bounded reads.
