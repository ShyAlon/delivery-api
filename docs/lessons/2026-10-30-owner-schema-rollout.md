# A migrated disposable smoke is not a migrated live database

## What failed

The cutover tests and image smoke applied the current migrations to disposable
Postgres. That proved the candidate schema but did not prove the existing live
project had been migrated or its app login had SET membership in new restricted
roles. The live startup preflight checked that too late, after model grounding and
image build. The ops plan lacked an explicit early live-schema gate. This was a
rollout/checklist gap, not a reason to weaken the startup checks or use an admin
login in the app. The Debug report PoolTimeout shows the report was not saved. It may be secondary
to startup failure or pool pressure; its exact cause is not established and must
be rechecked after schema readiness, not silently called fixed.

## Ordered rollout review from code and the reported preflight

No live connection was made to prepare this review. Catalog missing objects prove
absence of the five table-creating migrations' effects, not exact Supabase history.
The owner's script lists catalog state and optional migration-ledger state
separately. Existing objects are not a checksum proof. Partial objects or ledger
contradictions stop apply. Older migrations are never automatically replayed.

| File | Evidence / existing-data risk |
| --- | --- |
| 20261029203000_resource_metrics.sql | Four metric tables missing. Adds empty tables/indexes, restricted writer and source-bound RLS. No import or existing task/accounting changes. Grants DELETE privilege for retention, not a DELETE statement at install. Future metric sampling consumes DB space. |
| 20261029210000_generator_snapshots.sql | Table missing. Adds empty immutable BYTEA snapshot evidence with owner/task foreign keys and RLS. No old checkpoint import. Future payloads may be up to64MiB each, so free-tier DB/storage headroom must be checked before large runs. |
| 20261029220000_generator_documents.sql | Table missing. Adds empty mutable workflow document table with session/owner foreign keys and RLS. No history import or deletes. Future payloads likewise bounded at64MiB each, not a total-project quota. |
| 20261030010000_operator_log_records.sql | Both feature tables missing and writer probe fails. Adds bounded log rows and one startup projection per source, restricted writer/source RLS. Grants DELETE permission for log retention, no DELETE executed at migration time. Future logs consume storage. |
| 20261030020000_resource_metrics_bounds.sql | Not established by table report. Adds CHECK constraints and scans any existing metric rows; can fail on invalid historical samples. No automatic cleanup. Short lock/statement timeout makes this fail rather than hang. |
| 20261030030000_resource_metrics_maintenance.sql | Not established by table report. Creates a SECURITY DEFINER fixed metrics-only rollup function. Creation itself deletes no rows, but its body contains DELETE FROM for raw metric samples after successful summary insert. **Owner approved installation at16:45:41 IST.** The script permits only this exact filename+SHA256. Any changed contents or other destructive file refuses. The migration does not schedule or call the function. |
| 20261030040000_engine_quality_config.sql | Table missing and reader probe fails. Adds empty service-global quality config with fixed key and restricted read role. No file import or runtime writer. |

All seven are additive DDL at installation, except the maintenance function
contains future deletion behavior. None authorizes changing task, provider,
checkpoint or accounting history. DDL still needs brief locks and privileges.
The script uses lock_timeout5s and statement_timeout60s per migration, one
transaction per file; first failure rolls that file back, preserves earlier
APPLIED files and stops. It prints SQLSTATE and PostgreSQL primary error, not a
connection string. Exact server errors may contain database object/constraint
names. Do not send raw settings or data-bearing diagnostic details in chat.

## Owner-run path

`python3 scripts/owner/apply-pg-cutover.py .` is dry listing only. It loads
REQUI_ADMIN_DATABASE_URL and DELIVERY_DATABASE_URL through the shared private
file-first loader. Admin and app endpoint/port/database must match. It asks the
app connection for current_user rather than guessing a pooler role suffix.
`--apply` adds a separate typed `APPLY N MIGRATIONS TO role` gate. It refuses
partial/unapproved destructive pending files before any migration or grant. No live write
was performed by the assistant. No Supabase migration ledger is invented.

After approved migration work, fixed SET memberships are granted to the verified
app role with INHERIT FALSE, SET TRUE for requi_log_writer, requi_metrics_writer,
and requi_engine_config_read. No broad table/admin privilege is added. Rerun dry
inventory then the normal preview. Only the exact hash-bound approved maintenance file is exempt from the destructive scanner.

Preview step1 now names and runs a read-only catalog + app membership check,
before model grounding/build. The image's disposable PG and full startup
preflight remain independent checks. The guard is a readiness proof, not a full
schema checksum/security audit. Actual Supabase schema/history, free-tier
remaining storage, locks, owner permissions and owner-run apply are unverified.


Approved exception hash:
`20261030030000_resource_metrics_maintenance.sql`
`8bef488bdc0d6795624821d2cd06c6fb663276ccc27643bd2311ab0ec56cb07a`.
The migration itself neither schedules nor invokes the function. Existing runtime
metrics maintenance may invoke it later; this is not a promise retention never runs.

Validation: fail-first4 cases, exact exception regression, early-preview abort,
real owned-PG catalog/grant checks, and a fresh disposable database seeded only
through the previous migration then upgraded by the actual owner apply function.
The seven-file run and restricted grants passed. Partial/changed plans, wrong
confirmation and unsafe files stop; transaction test proves first-error rollback
and no later grant. None of this is a live Supabase apply or hosted CI proof.
