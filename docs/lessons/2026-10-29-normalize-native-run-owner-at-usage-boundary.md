# Normalize native run owners before identity checks

2026-10-29: native PG run rows return `owner_id` as a UUID. The legacy usage
endpoint compared it to the actor's string ID and passed it to a store method
whose scoped-owner check also expects a string. Same owner, different Python
types: authenticated owners got 403; local compatibility got access-denied/404.

Normalize that one endpoint owner value to string before both checks and debit.
Do not relax authorization or change the store's foreign-owner rejection.

Two fail-first cases exercise real restricted PG rows and the endpoint function,
with local/authenticated actors. Both fail before the fix. The authenticated case
also verifies another actor remains 403 with no extra debit. Persisted usage owner
and token counts are checked. Token-quota provisioning is an explicit test stub,
not a claim of full legacy account-flow coverage.

The public cloud mutation route is currently fenced by TenantMiddleware before
endpoint execution; prepaid provider settlement is a different path. This fix
repairs legacy usage wiring, not cloud execution admission.

Measured regression slice: 43 passed in 3.60s on Python 3.11.17, including PG
run isolation and cloud execution/API fences. Hosted CI/full suite not run.
No deployment, external database, provider execution or spend.
