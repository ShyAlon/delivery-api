# Read-only projection checks must not warm the cache

Check independent history formulas inside one repeatable-read, read-only snapshot.
Admission and expense totals have lazy clocks: compare at each recorded as_of,
never call a refresh function just because its name sounds like a read. If no
cache exists yet, report incomplete rather than passing zero totals or writing.
Retail backfill can be checked immediately against frozen exact rates.

Emit only status/counts, not owner IDs or amounts. Bound statement/lock time and
roll back the read transaction. This proves totals for initialized caches, not
chunk content, current-time expiry, a deployed runtime or production capacity.
