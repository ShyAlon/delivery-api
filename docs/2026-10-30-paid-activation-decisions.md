# Paid activation decisions: allow useful work, stop runaway cost

Decision date: 30 October 2026, 22:19:53 Asia/Jerusalem.

Shy approved all twelve proposals in `requi-paid-activation-decisions.xlsx`:

> Yes to all. Remember that the guiding principle is to allow actions to take place. Our goal is to prevent running costs - an error that might loop through LLM calls.

This records policy and the proposed reconciliation below. It does not install live settings, migrate data, deploy, spend, grant credits or enable an unsupported execution mode.

## Approved scope

| Question | Decision | Boundary |
| --- | --- | --- |
| Activation audience | Attended local preview first. | Public/cloud execution and unattended operation are separate decisions. |
| Own-test expense | Reaffirm $2 provider expense per rolling 24 hours for Shy's test profile. | Not retail credits, not a customer quota. Live configuration unverified. |
| Session tripwire | Reaffirm $3 confirmed provider expense per session. | Allow an admitted call to finish and settle; stop new calls after breach. |
| Platform tripwire | Reaffirm $3 x registered users per rolling 24 hours. | Shared platform total, not a per-owner daily quota. |
| Customer ceiling | Purchased prepaid credits, no additional interview/day quota. | The desired 50 credits/day is not an automatic grant or permission to spend. |
| Pricing | Keep reviewed price snapshots. Code defaults are $1 face value/credit, 0.0000125 credit/token and 0.0000005 credit/10ms compute. | Actual private runtime snapshot must be verified; provider expense does not set retail prices. |
| Model route | DeepSeek/deepseek-flash for paid work; explicitly selected Ollama for local preview. | No silent fallback. Provider prices still require current source evidence. |
| Live operations | Shy runs reviewed migration/settings/build scripts. | No assistant authority for live DB writes, deployments, resets, grants or spending. |
| Paid child mode | Prepare acceptance and implementation, not immediate paid enablement. | Current code explicitly refuses paid child execution. |
| Child acceptance | Parent-only provider/accounting authority; durable inputs and fences; Stop/kill/reap; crash-after-dispatch recovery. | Switch defaults and retire old thread path only after acceptance; explicit operator rollback, no automatic paid rerun. |
| Resource limits | Preserve existing reviewed mode-specific limits, including exactly three owner-wide calls in launch validator. | Call concurrency, run slots and DB pools differ. No cap/timeout increase here. |
| Alerts/recovery | Existing email route, exact sender/recipient and private transport readiness verified before launch; operator reviews unknown outcomes. | Outbox is not delivery; do not guess uncertain calls free or replay paid work. |
| Payments/gates | No real-payment activation or automatic content-gate decisions in this scope. | Restarting preview does not open payments or approve unattended spending. |

The pricing and payments points share a question in the workbook; the table expands them for clarity, not new authority.

## Proposed reconciliation: two profiles, not a hidden bypass

The old own-test onboarding script writes a $2 rolling-day `admission_limits.expense_limit`. The current prepaid launch validator deliberately requires that owner quota to be NULL. A cap row from testing can therefore prevent legitimate launch calls before the confirmed-expense tripwires are even consulted.

Keep the policies in separate explicit profiles:

1. **Own-test profile:** preserve the approved $2 rolling-24h provider-expense limit. Do not silently clear or ignore an installed cap. If this profile needs support on the modern gateway, implement and test a dedicated confirmed-expense test tripwire; do not make the launch validator accept arbitrary owner quotas.
2. **Prepaid launch profile:** retain no owner quota, no per-attempt cap or rate quota; purchased-credit admission and the approved $3 session / $3 x registered-user platform confirmed-expense tripwires protect the service. A reviewed owner-run transition must name any old test cap being replaced. Clearing it is not authorized merely because a validator rejects it.
3. **No automatic profile conversion:** report a test/launch mismatch clearly and prepare the exact owner-reviewed transition. Never turn it into an opaque provider failure, silently disable protection or repeat paid work. Changing policy is not accomplished by changing an error string.

This is the smallest policy reconciliation that keeps both approved purposes. It removes the false need for customer quotas without revoking Shy's separately reaffirmed own-test protection. It does not claim that two incompatible live configurations can operate together unchanged. The owner-run transition and any dedicated own-test guard remain implementation work, not completed by this document.

## What a tripwire means

Limits exist to catch looping/runaway code, not prevent normal use. Within reviewed balance, identity, provider and input rules, actions should proceed without repeated manual approval. At a confirmed-expense threshold, only new admissions pause; already-admitted calls finish and settle exactly once. Unknown outcomes remain held for operator review, never guessed as zero. These rules do not authorize automatic continuation through content gates or blind provider retries.

## Evidence and what remains unknown

- The approved workbook distinguishes prior decisions from new choices and keeps provider expense separate from credits.
- Historical task capture at 20:26: two settled retail entries total 0.022425 credits before compute. This is partial-stage evidence, not a completed interview forecast.
- Account-wide 73 settled calls and 2.114342 charged credits are not task-only totals.
- Captured raw provider units lacked their source quote scale. A conversion using the code's current scale is provisional, not verified provider USD. No full-interview/day percentile or new numeric ceiling was inferred.
- `launch_admission_policy.py`, the now-retired preparation-only expense policy, `CONFIRMED-EXPENSE-GUARD.md` and `scripts/ops/grant-credits.sh` establish the configuration conflict; none proves installed live values.
- Both child-process work items concern phase-5 acceptance/default cutover/retiring threads. Their local fake-provider tests do not prove paid-child readiness.
- Hosted CI, Docker, private settings, SMTP delivery, production policies and the currently running preview remain unverified by this document.

## Follow-through, no new manual gates

Before implementing a profile transition, inspect the current owner-run configuration path and guard semantics, reproduce the mismatch in hermetic tests, and propose the narrow change. Keep normal UI actions free of new approval prompts. Use existing operator review only for actual live configuration effects and uncertain-money recovery.
