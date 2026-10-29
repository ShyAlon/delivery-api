# Shared review setup has external callers

2026-10-29: ae52029 made decision-page setup require a PG workspace and gave
its session a tenant-specific name. Gate-review and stack-decision consumers
were missed: 19 failures and 1 pass on unchanged main. Passing a workspace alone
still leaves their artifact bodies/paths bound to obsolete session-one.

Pass the real PG workspace through every helper. Bind artifact session fields,
paths, semantic reviews and lineage to the actual engine session. Keep local
mutation contracts in the existing separate PG-local subprocess profile, not by
removing default cloud middleware. Make the shared helper's task/session names
unique per call because the subprocess profile reuses its seeded owner. Keep
producer markers on the collected tests. No tests or assertions are removed.

Measured: 28 passed in 23.05s current and 28 passed in 22.46s under the held
PG-first bootstrap for the two migrated modules plus shared decision tests.
All retained producer cases ran here using installed producer dependencies.
The profile injects no cloud admission and the endpoint-local actor remains the
original local user. This is local wiring coverage, not cloud mutation support.
Hosted CI/full suite not run. No production/default-bootstrap change, coverage
loss, deployment, external database, provider execution or spend.
