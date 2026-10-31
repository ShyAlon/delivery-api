# Keep the fake capacity probe small and honest

Do not build another provider framework. Use a fixed sleep and canned usage row
while exercising real tenant RLS, worker claims, shared pool, run slots, append
chunks and usage projections in disposable PG. Deny network sockets and inherited
DB configuration before startup. No external models, accounts or provider calls.

Run: .venv/bin/python scripts/bench/capacity-baseline.py --output /tmp/capacity.json

The fixed 1/2/4-user cohorts report queue maximum, per-work maximum, cumulative
checkout time per operation (not a single-checkout percentile), pool counters,
sampled DB connections/lock waiters and host memory pressure. Explicit thresholds
flag each dimension; these are local report flags, not deployed alerts. Sampling
can miss brief lock waits. Pool counters are process-cumulative across cohorts.

This is a bounded control-plane baseline, not a complete generator pipeline or
an infrastructure sizing result. Real network latency, provider behavior, full
artifact payloads and model CPU are excluded. At default two slots, higher-user
cohorts intentionally wait. Do not present these seconds as production capacity.
