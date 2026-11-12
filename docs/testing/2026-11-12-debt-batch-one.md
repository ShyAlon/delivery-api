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

## Verification

All checks use disposable PG, real local HTTP/browser/process boundaries and
stubbed model/provider responses. They do not use paid credentials, a live DB or
owner settings. Explicit DELIVERY_TEST_ENGINE_SOURCE,
DELIVERY_TEST_GENERATOR_SOURCE and DELIVERY_TEST_SPEC_SOURCE select the installed
sibling sources. Missing inputs are not successful execution.

Completed local evidence:

- Source-enabled fast gate in two disjoint halves: 2,437 passed, two optional
  skips. This includes the egress guard with the installed engine dependencies.
- All 35 historical PG retained-snapshot/successor-matrix nodes pass in bounded
  groups. Another 52 PG scope/store cases pass outside that matrix.
- All nine crosshost acceptance cases pass in completed bounded groups, including
  the full session alternating hosts through validation and post-accept
  navigation recovery. The shared founder/operator/pin setup lives in a fresh
  owned DB and is force-dropped afterward, never lent to accounting tests.
- All 167 non-cosmetic main UI nodes exercised in bounded groups. The stale
  readiness/layout fixtures have passing exact reruns. Other browser modules
  were exercised in bounded groups, including real review/composer/tracing and
  final-package journeys. This is not a claim that the full 526-file collection
  ran in one invocation; no hosted CI pass is implied.
- Real installed/default/legacy child and generator runtime group: 35 passed.
- Startup inventory, diagnostics and fresh local/fake-paid preflight: 69 passed.
- Mixed UI/ledger/operator ownership: 43 passed. Store-constructor/harness and
  failure cleanup group: 40 passed. Connected item6 PG runner: three passed.
- UI boundaries, operator module imports (Node's experimental VM-modules flag),
  process-control boundary and diff whitespace checks pass.

The broad heavy sweep exposed stale startup inventory, missing explicit source
inputs and stale UI contracts. Each was fixed and rerun with the relevant source
inputs. Interrupted runs were never counted as final results.

## Product defects found by unchanged behavior assertions

A missing child registry session raised an untyped KeyError during task reads,
masking saved artifacts. The narrow typed404 catch leaves serialization errors
visible instead of mislabeling them missing. Retained final-package and actual
runtime tests pass.

A failed answer hid its current question without restoring it. The existing
browser assertion failed first. Restore preserves the original visibility and
only affects the still-current submitting render. Screenshot inspection then
caught stale processing progress after503, so a new assertion proved that issue
before the settlement handler was fixed. Six accepted-answer/next-question/
cancel/failure cases and eight shadow/cancel/stale cases pass. The inspected
screenshot shows the original question, retained draft, retry button and honest
waiting-for-you status. Successful accepted answers remain disabled while the
next input is awaited; no automatic replay was added.

The final fast sweep exposed a watchdog shutdown race. A new immediate-exit
assertion failed before stop owned its thread join. Stop now waits at most one
second and never joins itself. Egress-test startup uses this run's installed
interpreter rather than demanding a second sibling virtualenv.

No new migration was created. Startup inventory now includes the two existing
answer-accounting tables and their normal backend access. No billing policy,
admission limit, cancellation rule, model route or paid execution changed.
