# A failed PG metrics profile must not create a local DB

An explicit postgres metrics backend now routes sampling, rollup and operator
views through the merged shared-pool repository. Stable source identity is
required; unknown backend, missing source and connection failure produce the
existing unavailable diagnostic and never fall back to SQLite. Existing admin
HTTP guards still gate resource views; this source-scoped repository is not a
new cross-host/customer read surface. Existing SQLite path remains only pending
its separately scoped removal after replacement merge. No historical import.

Fail-first3failed0.15s with SQLite poison. New tests cover sample->restart->read,
rollup, container/overview, bad/missing config and connection failure. Original
metrics/pool tests stay unchanged. Explicit DELIVERY_METRICS_BACKEND=postgres
requires schema + process-role rollout; no live grant/activation in this PR.
