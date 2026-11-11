# Debit once, process, correct once

## Problem and selected behavior
The current provider path repeats database payment work inside one answer. The owner selected0.1 credit as the default fixed answer-start debit on11 November2026, adjustable as data arrives. The start debit is not an allowance or maximum; actual can exceed it. Existing reconstructed data included an answer around0.1458 credits, so a positive correction is expected, not grounds to stop.

`AnswerPricing.from_environment(env)` reads `DELIVERY_ANSWER_START_CREDITS` (default`0.1`) and the existing `PRICE_PER_TOKEN`, `PRICE_PER_10MS`, `USD_PER_CREDIT` settings. It freezes an immutable snapshot. At1e9 account units per credit, default E is100000000 units. Explicit invalid values fail config validation instead of becoming a default. No runtime environment/startup behavior is changed by this module alone.

One additive migration supplies `answer_charge_receipts`, final-only `answer_charge_usage`, `requi_answer_start` and `requi_answer_finish`. Functions are invoker-only, backend-executable with forced owner RLS; browser/anon/service-role defaults are revoked. The trusted backend supplies normalized usage and configuration, as with the existing retail ledger. This is not a malicious-backend boundary: that role already has account UPDATE authority. Browser data must never choose E/rates/actual.

Start needs an already owned current-generation job and original input identity/digest. It checks/debits E under the account lock. Same session/generation/input retries return the original receipt even if a retry enqueued another job, the network request id changed or current config changed. The runtime must reuse the original receipt/job, not run the replacement job. Finish bulk accepts normalized final attempt evidence and deterministic duration, computes actual from the frozen formula and the CURRENT account remainder under lock, applies A-E and stores the correction once. Attempts are unique across answers; conflicts roll back the entire correction. It settles accepted usage even if the job finished or task generation changed. Final publication authorization is a separate CAS to be folded into runtime integration, not a reason to suppress consumed accounting.

Missing correction retains E. Unknown usage is not synthesized as zero. E has no upper/lower relation to actual, no extra cap or incomplete-answer-count gate. Overrun is stored on every settled receipt alongside E/A/correction and timestamps. Integration will emit one structured log summary from the receipt for every answer, not only overruns; no extra payment round trip. This PR does not introduce an auto-refund, recovery scheduler, durable journal or per-provider send marker.

## Deployment and merge order
This is foundation only. No application/engine/gateway calls these functions. Existing jobs retain existing accounting. The schema inventory recognizes the new migration; a rebuild that checks current schema will see it as pending until the owner applies it. Merging this PR alone does not change debit behavior, but it is not a claim that startup schema checks ignore the new objects.

Next delivery integration selects immutable accounting mode only after start+provider bypass+finish+display/audit projection+publication are complete; old jobs stay legacy to avoid double debit. Engine compatibility/checkpoint batching must land before selecting the new runtime. Runtime activation/cutover is not authorized by merging the foundation. No live schema/rebuild/paid run should be performed merely to inspect this PR.

## Verified locally
The repo test harness provisions disposable PostgreSQL, authenticates the restricted backend role and applies the migration there. No live DB/server/provider access. Focused pricing, retail, credit ledger, prepaid, tenant/RLS and schema-inventory tests are listed in the PR result. SQL function tests are not full HTTP lifecycle or production pooler proof.

Two local pipeline Sync protocol barriers include BEGIN, role+identity/session, function and COMMIT for each operation. TCP packet counts, pool checks, production network pooler and total whole-answer sync count remain unmeasured. The10-17 full-answer budget is an estimate from the integration design, not this PR's result. Future acceptance must count delivery and engine traffic plus final correction, logs and connection setup.

Verified focused command (142 passed, one existing XDG_RUNTIME_DIR warning):

```
PYTHONPATH=src python -m pytest -p no:cacheprovider tests/test_answer_pricing.py tests/test_answer_accounting_db.py tests/test_retail_pricing.py tests/test_credit_ledger_db.py tests/test_pg_native_provider_credit.py tests/test_retail_ledger.py tests/test_grant_credits.py tests/test_pg_harness.py tests/test_free_local_prepaid.py tests/test_owner_schema_readiness.py tests/test_migration_identity.py tests/test_migration_target_difference.py tests/test_supabase_sessions.py tests/test_tenant_rls.py -q
```

The inventory test exercised the owner schema-apply implementation against a separate disposable PostgreSQL fixture, including new catalog probes and default Supabase ACLs. This is not a verified end-to-end run of the owner's `req rebuild --apply` workflow, and is not a recommendation to run it now.
