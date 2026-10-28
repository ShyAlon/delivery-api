# PG fixtures need real session boundaries, 2026-10-28

A fast factory must not replace tenant identity with a global superuser or reuse
owner UUIDs as session IDs. Seed fresh owners and live distinct auth sessions,
borrow the real restricted application pool, and run real auth middleware with
locally signed JWTs. Keep migrations once per process, not once per test.

A cold-only green run missed a harness ownership bug: a second pytest invocation
reclassified the same disposable DSN as external. Test repeated start before
claiming warm performance. Keep the refusal of truly external DSNs intact.

Report scope differences with measurements. A smaller browser fixture being faster
does not prove that PostgreSQL made an equivalent full suite faster.
