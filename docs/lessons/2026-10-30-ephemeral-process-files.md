# Process logs are ephemeral; audit stays durable

Owner clarification30Oct18:07:05 supersedes PG operational log storage.
PR530 merged atmaina0c43dd. Keep bounded nonblocking admission, replace its PG
sink with rotating files under the writable server data dir (Docker /tmp).
No log SQL, role, source ID or supervisor pool required. No destructive migration:
old PG log tables/rows are left alone and legacy repository contract tests remain.

activity, diagnostics, engine stdout, stage logs, startup report are operational.
None of operator_log_records is the authoritative audit store. audit_log, usage,
provider attempts, task events, credit/payment ledger and compute records stay DB.
Resource metrics are not reclassified by this PR and stay DB as previously agreed.

Files: same32MiB budget across fixed streams, active+one backup, bounded1024 queue,
32KiB admission cap, private file mode, cross-process engine rotation lock.
All disk I/O occurs on writer thread, not caller. Overflow/disk failure/drain timeout
is explicit degraded status. Normal shutdown bounded drain; sudden loss is OK.
Debug reads recent server file rows, registry logs.read checked for global view.
Downloads still omit engine stdout and diagnostic source frames. Customer engine
reads retain session attribution. Startup report is atomic ephemeral JSON.
Missing historical log schema/role no longer blocks startup/schema readiness.
The supervisor has no logging DB pool; default budget returns10+4=14/15.

Fail-first: new file-sink test failed on absent sink before implementation.
A DB append trap plus slow disk regression proves process logging makes no DB
write. Reproducible3x150ms sink measurement: synchronous450.47ms vs asynchronous
file admission0.590ms, drain454.41ms off caller. Earlier PG before measurement
450.77ms;530 already moved that caller wait off loop, so this PR's incremental
benefit is zero operational-log DB transactions/pool pressure, not a further
claimed live throughput improvement. Actual owner Mac/Supabase before/after,
Docker and hosted CI not run. Real Chromium anonymous boot and disposable PG
accounting/audit regression suites are the local validation boundary.

Old runtime tests asserting no files/PG routing were replaced, not skipped:
file restart reader, bounded rotation, failure, projection, exact task filter,
registry permission, download redaction and PG-write trap cover the new contract.
Legacy PG table tests stay as historical non-runtime API checks. No audit schema
or audit write implementation changed. No live DB writes, deploy or spend.
