# Full-run readiness,31October2026

This is a source/test audit, not live acceptance. No production DB writes, deploy, paid calls or payment activation were performed. Owner approval of policy does not prove installation. A green partial test is not an end-to-end claim.

## Merge and execution order

-539 records the approved12-row policy and is already in main79338834.
-540 fixes old answer callbacks overwriting the next question. Independent of money/data; no stored transcript rewrite.
-541 implements the operator-selected $2own-test confirmed-cost tripwire on the actual DeepSeek dispatch path, with explicit owner-run transition. Apply its migration and start the reviewed runtime BEFORE removing the legacy cap. It does not enable the session/platform launch guard.
-542 aligns diagnostic tests with the bounded-file log cutover, preserving scope/redaction coverage and keeping historical PG adapter coverage explicit.
-543 moves unchanged operator usage readiness SQL behind its verified reader. No grant or response-contract change.
-This documentation/test-harness PR names readiness boundaries and repairs two stale harness entry assumptions. It does not activate anything.

These themes touch separate main files except tests/test_tasks.py in542 and app.py in543 (different files, no textual conflict). Recheck branch bases/CI at merge time. Do not use the checkout update performed by an ops script as proof an older running process was replaced.

## What was actually demonstrated

1.Meditator7.97 display race reproduced through actual task-page submit and SSE handler. The POST lasted154315ms; next-question render preceded response by about9seconds. Saved answer event356 binds correctly to original input1ca3b37e. Before fix the old callback reads new DOM and pairs Q-next with A-previous. New regression fails before fix;20focused browser contracts pass afterward. Pixels show next question and empty enabled composer, no stale processing card.
2.The existing fake-model, real-UI, real-owned-PG cross-host test `test_full_session_alternates_hosts_through_validation` passed in66.70seconds. It verifies all declared artifacts/approval companions published and terminal phase approvals. It does not create/download a final package and is not a paid production run.
3.Local real zero-model generator handoff endpoint and immutable package/download tests pass (part of28selected package/engine contracts). These use retained fixture files, not the all-phase test's final cloud state. Do not concatenate separate passing tests into one full-run claim.
4.Own-test dispatch and transition tests cover rolling expense, accepted-call settlement, concurrent settlement/start serialization, scale/unknown/future-history fences, tenant/profile visibility, actual production factory start/refusal cleanup and local-route exclusion.122selected money/ops tests passed without producer-source skips.
5.Diagnostic contracts:147tests passed after the file-sink reader changes; a remaining stage-path contract was subsequently updated and passed in isolation. HTTP readiness cleanup:169selected tests passed, with that separately fixed old-stage case explicitly deselected in that run. These are not whole-suite results.

## Exact owner path, and blockers

1.Merge reviewed PRs. Run normal schema readiness/migration workflow. Restart/rebuild preview using `bash scripts/ops/preview-exec.sh` from the delivery-api checkout. Confirm the displayed runtime four-commit tuple matches the build, not merely the candidate build line. No automatic graceful drain is inferred from restarting; stop/quiesce/reconcile work first.
2.For the own-test profile, follow541's `docs/2026-10-30-own-test-profile-and-full-run.md`. Run `bash scripts/ops/own-test-profile.sh` only once the new dispatch guard is running. Review email, exact old$2/86400cap, expense scale/history and proposed profile. Typed acknowledgment/yes is owner confirmation, not machine attestation. Profile/cap transition is atomic; no task is started. Do not roll runtime back to unguarded code with a NULL legacy cap.
3.Verify reviewed DeepSeek model/vendor prices and1e12expense units/USD, positive purchased/test credit balance, provider balance/key, selected model route and no unresolved provider/compute accounting. Existing $2profile is a loop tripwire, not a guaranteed total bill: accepted concurrent calls can overshoot. No full-interview cost distribution is measured.
4.Session/platform launch protection is a distinct blocker. ProductionGateways currently creates paid gateways with launch_guard=False. The approved$3/session and$3xregistered-users/rolling24h protection requires explicit policy installation and runtime rollout; validators existing in source do not prove those guards run.541independently protects selected own-test dispatch but does not silently switch launch mode.
5.Paid child execution is still explicitly refused in ChildInterviewClient and ChildGeneratorEngine. Parent-owned broker/ledger/Stop/crash recovery pieces exist and have fake-provider tests, but enabling paid routes by deleting refusal checks is not safe implementation. Paid-child acceptance/cutover remains separate work. Local child mode remains opt-in Ollama-only.
6.Cloud final-package create AND download currently return503: customer-safe diagnostics/export contract pending. Do not remove these guards merely to get a green button. Local package success is not cloud package readiness. An actual completed cloud workflow must include a safe immutable package from the same authoritative final revision, with owner boundary and download verification.
7.SMTP sender/recipient, transport delivery/retry and alert recovery remain unverified. An outbox row is not email delivery. Real payment receiving and automatic content-gate approval remain outside this scope.
8.When those blockers are implemented and accepted, Shy can execute a paid task himself with manual gates, not an unattended paid rerun. Completion evidence must cover all phases, final package validation/download and settled token+compute costs. Stop on loops or unresolved accounting; never guess unknown usage free or auto-repeat paid calls.

## Reproducible hermetic checks

From the delivery-api checkout, with the sibling repositories checked out:

```
PYTHONPATH="$PWD:$PWD/src" \
DELIVERY_TEST_ENGINE_SOURCE="$(dirname "$PWD")/interview-engine" \
DELIVERY_TEST_SPEC_SOURCE="$(dirname "$PWD")/spec-generator" \
DELIVERY_TEST_GENERATOR_SOURCE="$(dirname "$PWD")/spec-generator" \
SPEC_GENERATOR_DIR="$(dirname "$PWD")/spec-generator" \
.venv/bin/pytest -q tests/test_crosshost_interview_acceptance.py -k full_session_alternates
```

This uses owned disposable PG and deterministic fake models. The test docstring's historical approval label is metadata, not authorization to contact external providers. No production secret is needed. Package integration is a separate `tests/test_package_integration.py tests/test_final_package.py` selection.

## Broad-audit limits and remaining debt

No whole-suite-green claim. An unrestricted run first hit four `tests.*` import errors; repository root in PYTHONPATH resolves that collection issue. Whole run exceeded bounded execution around16percent. Batches0,1,5,6,7,8,10,11,14finished green; batch7still skipped29private payment-DB tests due missing explicitly isolated payment credentials. Batches2,9,12timed out and are incomplete, not passed. First-red runs on3and13identified stale settings/stage contracts and HTTP SQL; item6graph source-path mismatch found in4. This PR repairs only the two named harness assumptions below. Image-build fake SSH is stale against remote-scratch smoke staging (`smoke-image: invalid remote scratch`); no real server/Docker was contacted and that test group is not green. Hosted CI, actual image build and private runtime remain unverified.

Test changes in this PR: grant-credits static hidden-input test now follows shared ops_settings and tests no secret argv, rather than requiring removed shell `read -rs` spelling; existing real PTY no-echo test still runs. Full generator graph fixture accepts either a checkout root or its src directory consistently; invalid roots fail explicitly. No assertions removed or test retired.29harness/settings/PTY tests passed. Other PRs disclose their own changed-test inventory.
