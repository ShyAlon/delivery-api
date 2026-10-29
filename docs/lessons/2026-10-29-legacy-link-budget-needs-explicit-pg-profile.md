# Legacy link coverage needs an explicit PG-local profile

2026-10-29: the default PG-first app correctly fences legacy /links and /runs
mutations. Do not disable its tenant middleware to make unsigned local tests
pass. The owner chose to keep the budget-402 and both 403 contracts on real PG
with an explicit local actor, and retire only the lifecycle/one-active-run-409
case. The owner accepted that narrowly identified coverage loss.

Use the existing isolated PG-local subprocess profile. Inject the seeded scoped
owner identity, and explicitly disable retired local account/quota provisioning
in that profile only. Restricted PG and poisoned SQLite remain enforced. These
are local endpoint wiring tests, not cloud admission or auth proofs.

Fail-first: unchanged current API was 18 passed; held PG-first bootstrap was
4 failed/14 passed. The 3 retained cases preserve their original 402/403 and
budget_stopped assertions. The separate UUID-owner fix (#457) was needed before
the budget debit could reach its guard.

After migration on merged #457: current and held-PG-first API, cloud API,
owner-boundary and native PG run/usage slices each have 56 passing cases
(7.73s current; 8.03s future). The lifecycle test alone is retired;
no production code or default bootstrap is changed. Hosted CI/full suite not run.
No provider calls, external DB, deployment or spend.
