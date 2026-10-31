# Paid-child activation: decisions and prerequisites

31 October 2026. Code audit only. No paid calls, live DB inspection/writes, settings changes, deployment or activation.

## Current state

Paid-child acceptance covers actual fresh/reaped children with fake providers, exact parent token/compute settlement, Stop/kill, crash/replay, durable founder gates and cross-provider route switch. The default in-process and sampled-CPU alternatives are removed. `DELIVERY_CHILD_EXECUTION=paid` still refuses; valid price settings cannot activate the removed default. Constructor-level paid refusals remain unless a typed parent runtime is installed. Cloud runs stay closed until the work below is implemented, reviewed and explicitly activated.

## Decisions for Shy

| Decision | Recommendation | Why |
| --- | --- | --- |
| First activation audience | Attended local preview only, manual content gates. | Public cloud, unattended runs and payment receiving are separate decisions. |
| Profile for first paid child run | Keep own-test $2 rolling-24h protection and add confirmed session/platform launch protection together. Implement the compatibility explicitly before activation. | Do not clear the test cap to pass a validator, silently convert profiles or bypass the $3 guard. |
| Activation authority | Approve the resulting code/settings/script separately after prerequisite evidence is complete. Shy runs live scripts and the paid task. | This sheet does not authorize live effects or spend. |

## Blocking implementation and verification

1. **Real dispatch path:** `ProductionGateways.__call__` currently constructs `WorkerAdmissionGateway` without `launch_guard=True`. The typed paid runtime requires it. Install the reviewed typed runtime and launch guard together; keep atomic reserve, journal binding, fresh authority, prepaid and guard checks before HTTP. Prove refusal means zero HTTP and accepted-call settlement remains possible after Stop or a tripwire breach.
2. **Profile consistency:** launch owner validation requires all owner quotas/attempt caps/rate limits NULL and exactly three owner-wide concurrent calls. The own-test profile has distinct $2 confirmed-expense rules. Preserve its approved protection through an explicit implementation/transition, not by relaxing launch validation for arbitrary limits. Existing `requi_own_test_expense_guard` runs on DeepSeek start; verify how it composes with the launch path. Test both profiles and changed/absent/mismatched profiles fail closed.
3. **Confirmed tripwires:** verify the installed session $3 and platform $3 x current registered users / rolling 24h policy, expense unit scale and shared settlement/start lock. Unknown outcomes block new admissions; admitted calls finish and settle once. Source code and fake policy rows do not prove live settings. Prepare read-only preflight and exact owner-run installation steps.
4. **Configuration and evidence:** reviewed DeepSeek model, current vendor price snapshot/context ceiling, prepaid scale/formula, positive credits, provider balance/key, original Auth-session/RLS claims, DB pool budget and no unresolved provider/compute evidence. Keep keys/HTTP and accounting in the parent. Do not guess missing usage as zero.
5. **Alerts and recovery:** verify authorized SMTP identity/recipient, transport, retries and delivery receipt separately. An outbox row is not delivery. Verify Stop/reap and uncertain-outcome reconciliation against the installed runtime; never automatically repeat paid calls.
6. **Before cloud deployment:** add the deferred clear unavailable/readiness presentation if activation remains closed. Verify hosted CI, build tuple, migrations/Auth, runtime profile, paid selector and explicit rollback procedure. No silent fallback to retired execution.
7. **Actual run:** after separate activation/spend approval, Shy runs the attended task and records all phases, gates, authoritative final package create/download and settled token + compute totals. Fake acceptance is not a paid full-run claim. Cloud package endpoints and payments have their own current fences and must be checked rather than assumed ready.

## Source map

Repository sources: `child_paid_runtime.py`, `child_pool_budget.py`, `cloud_runtime.py`, `production_gateway.py`, `producer_admission.py`, `postgres_store.py::admit_provider_attempt/start_launch_provider_attempt`, `launch_admission_policy.py`, `attempt_ledger.py`, `CONFIRMED-EXPENSE-GUARD.md`, `docs/2026-10-30-own-test-profile-and-full-run.md`, `docs/2026-10-30-paid-activation-decisions.md`. Live policy, running preview, alerts and hosted CI were not checked.
