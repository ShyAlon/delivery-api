# Metrics sampling has no request tenant

Postgres-only durable storage includes run_metrics.db, not only task checkpoints.
An idle container sampler has no authenticated request. Do not borrow ambient
claims or make metrics customer-readable to fit the existing request store.

Additive resource tables have forced RLS, no customer/default-Supabase grants,
a process metrics-writer role scoped by stable source identity and operator
logs.read policy. No process role membership or live migration is installed by
this code. Use the existing one process pool. Appends and rollups serialize per
source; summary+raw deletion is atomic. Late samples after rollup refuse rather
than replace a permanent summary. Conflicting run identities refuse atomically.

Fail first:1failed3errors0.12s missing repository. Final26passed1.12s current /
1.50s heldPG-first before the final sequence ACL tightening; same set rerun after.
Old metrics and pool tests unchanged. This is a foundation, not completed sampler
cutover: no activation, importer, operator API wiring or SQLite path deletion.
Live role membership, historical-data choices and final cutover remain separate.
