# Owner test tasks

Apply `supabase/migrations/20261102220000_owner_test_tasks.sql` yourself before using this feature. No automatic owner provisioning and no live migration was run. The signed-in account must be the enabled reviewed operator with `sessions.operate`.

- POST `/api/operations/test-tasks` with normal title/brief creates a task with an immutable test marker. The customer creation route rejects extra marker fields.
- GET `/api/operations/test-tasks` is the bounded owner test view, including purged tombstones. Normal task lists omit test tasks. Product downloads and packages refuse them. Debug evidence remains available for failures.
- POST `/api/operations/test-tasks/{task_id}/purge` with `{"confirm": true}` requests an idempotent content purge only after a successful, settled, reaped Start and answer cycle. Failed/live/unsettled work stays intact.

The purge is not permission to run a paid test. Real paid execution requires a separate approved ceiling. All actual test charges remain in balances, spending ceilings, reconciliation and operational accounting. The retained immutable task marker tags the money through the job-to-task relation. It cannot be used to make spend disappear. No change to accounting totals or audit rows is made by purge.

The initial purge is deliberately limited to the child acceptance path without a source directory or private revision lineage. Unknown external paths, shared checkpoint threads and unreviewed artifact refs refuse. Source files outside PostgreSQL are not deleted. Operational logs and audit are retained.
