# Debt batch one: current callers and test ownership

This batch repairs the current regression proof before retiring payment/storage
paths. It does not change billing policy, activate a route, migrate a live
schema, merge a PR or run a paid provider.

## Reconciled backlog

- Disposable PG accounting harness already exists. Linked-task retail evidence
  and event-only transcript tests already pass. Do not rebuild retired recovery.
- Item6 connected graph already has its reviewed test policy: three connected
  tests pass through scripts/ci/test-item6-pg.py.
- Browser acceptance still used a retired cookie environment setting. Its actual
  HTTP origin now controls cookies, preserving signature/session/CSRF checks.
- Long acceptance runs now own a separate migrated, seeded disposable database.
  Founder credit grants, operator principals and reviewed pins no longer poison
  subsequent accounting tests. Failure cleanup has its own test.
- Account balance/ledger doubles use the real store constructor and one shared
  fake SQL boundary. Transaction ownership is asserted at that boundary.
- Default installed-child commands and installed gateway adapters share helpers.
  Custom PID-writing crash launchers remain separate.
- Concept review opens automatically. Lifecycle and tracing tests wait for that
  real route, not a launcher that may already have unmounted. Composer tests hold
  snapshot delivery while observing a transient shadow, not product execution.
- Customer availability text never directs admins to private operations pages.
  Machine-readable reason codes and fail-closed availability remain unchanged.
- Fresh-start preflight points its already-imported model module to fake HTTP.
- Missing child snapshot becomes a typed read error so saved artifacts/final
  package remain accessible. Serialization errors are not mislabeled missing.
- Answer helpers use the existing explicit PG operation registry, not a second
  decorator-registration path. Their transaction wrapping is unchanged.

## Verification status

Current partial evidence: 2,431 fast passes and four existing skips before the
last registry change; focused real child, installed default/legacy, accounting,
UI and fake-provider journeys pass. Wider regression sweep is still in progress.
Do not describe this as a full-suite pass or merge-ready until final verification.

Run tests with explicit DELIVERY_TEST_ENGINE_SOURCE,
DELIVERY_TEST_GENERATOR_SOURCE and DELIVERY_TEST_SPEC_SOURCE when checking sibling
source integrations. Missing inputs are not successful execution. No real vendor
credentials or external database are used.
