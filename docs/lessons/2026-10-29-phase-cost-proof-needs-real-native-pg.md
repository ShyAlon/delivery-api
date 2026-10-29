# Phase-cost proof needs real native PG, not a retired SQL fake

2026-10-29: three phase-total tests still exposed only SQLite-style _db.execute.
The production helper now uses _execute_native under a scoped PG transaction,
so all three failed with AttributeError on unchanged main. Replace the fake
with disposable restricted PG, real owner/session/task/job and exact retail
contribution posting using test-only rates and credits.

Keep the original aggregation/sort/sum, credits-only and empty-result contracts.
Replace the obsolete SQL-string assertion (SELECT/task_id=?/tuple) with behavioral
proof: another task's contributions are excluded; another owner sees nothing;
rows, exact totals, account balance and remainder stay unchanged by the read.
This deliberately loses only brittle SQLite placeholder/query-text inspection,
not its task-filter/read-only intent. No tests or production code are deleted.

Fail-first 3 failed,4 passed(0.25s). Final cost/native-retail/cloud-fence slice:
48 passed current(4.01s),48 passed held PG-first(4.21s),Python3.11.17.
HostedCI/full suite not run. No default bootstrap change, deploy, external DB,
provider calls or spend. The debits are only throwaway test data.
