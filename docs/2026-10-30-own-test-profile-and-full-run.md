# Own-test tripwire and the route to a complete run

## What changes

An operator-selected own-test profile checks $2 of confirmed DeepSeek expense in the trailing24hours immediately before dispatch, on the real production transition path, including when launch_guard=False. The profile is empty on migration installation. No account is selected automatically. Other owners and explicit local Ollama execution do not inherit this test policy. The existing launch validator remains strict: no owner quota, per-attempt cap or attempt-rate limit and exactly three concurrent calls.

This is a loop-cost tripwire, not a hard maximum bill. Accepted calls finish, settle and debit even after crossing. Up to three concurrently admitted calls can overshoot $2. Worst-case reservation size no longer blocks the test after the explicit transition. Unknown usage, missing/mixed expense scales and unreconciled charges are not guessed as free. The profile does not create an email alert or replace the session/platform alert mechanism.

## Owner-run order

1.Merge this PR; apply migrations with the normal reviewed schema workflow. Do not drop the old cap yet.
2.Stop and reconcile owner work. Start the reviewed runtime containing the new dispatch guard. Keep the old cap until that code is actually running. The transition's typed runtime acknowledgment is a human verification, not proof that an old server was upgraded.
3.Run `bash scripts/ops/own-test-profile.sh` from the delivery-api checkout, or `ops own-test-profile`. The shared launcher updates the checkout; this does not update an already-running process. The script reads the admin connection through the existing settings flow, asks for the signed-up email, previews the exact change and requires the runtime acknowledgment plus yes. It checks again in the committing transaction.
4.It replaces only an exact $2/86400-second legacy cap, requiring otherwise launch-compatible limits and quiescent/reconciled work. Profile installation and legacy-cap removal commit together. Recent paid history must explicitly carry the same1e12units/USD scale; unverifiable historical rows are not modified. A rerun verifies the same profile. Onboarding will not put the old cap back over a modern profile.
5.Record the output. No task is started, credits granted, launch guard installed, public/paid-child execution enabled or provider contacted by this transition. Do not roll runtime back to a version lacking the dispatch guard while retaining the modern profile and NULL legacy cap.

## Remaining full-run blockers and acceptance

- ProductionGateways currently uses launch_guard=False. This own-test guard is on its actual dispatch path, but separate $3session/$3xregistered-users platform guard activation still needs reviewed policy installation and an explicit runtime rollout. Do not claim launch protection simply from a successful own-test transition.
- Existing paid-child refusal remains. Paid child execution needs its own parent-owned dispatch/settlement, capability/Stop/crash recovery implementation and acceptance before activation.
- Confirm current runtime tuple, schema readiness, reviewed model/vendor expense scales, positive prepaid balance, provider balance/key, route selection and unresolved attempt/compute accounting. Approval is not proof of installed settings.
- SMTP destination, delivery and retries remain unverified. This PR does not activate payment receiving or automatic content approvals.
- First run a hermetic fake-provider whole-workflow acceptance using the real task UI and worker boundaries. Then Shy may run a paid full task himself under his reviewed settings, manual phase gates and expense monitoring. A successful answer or phase is not a full run: retain evidence through final package validation/download and settled token+compute charges. Stop on a repeated-question/call loop, unresolved accounting or provider challenge; do not auto-retry paid work.

## Validation and coverage

New PG tests cover explicit profile, quote larger than tripwire, accepted-call settlement after crossing, new-dispatch denial, rolling-window expiry, scale/unknown denial, foreign worker fence, hidden profile table, atomic/idempotent transition, active-work/nonstandard-cap refusal, actual ProductionGateways gateway-start path and serialized settlement/start race. Remote transport is replaced; dispatch/ledger/PG paths are real. No existing tests changed or retired. Existing producer-source-dependent tests are run with both local source roots, not silently skipped. Shell and embedded Python syntax checked.

No live database writes, deploy or paid run was performed. Hosted CI, Docker, owner-run script against real settings, installed schema/code and complete paid run remain unverified.
