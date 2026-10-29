# Remove legacy link token-quota tests,not credit safety (2026-10-29)

Owner11-test cleanup,theme3 removes exactly5:
- test_local_mode_bypass_unchanged
- test_exhausted_user_rejected_and_quota_decision_audited
- test_admin_can_change_user_quota
- test_usage_that_crosses_quota_is_rejected
- test_usage_tokens_are_read_from_pipeline_json

Unused _oauth setup helper/extra blank lines removed.70test lines removed.
No production change. Only direct tests found for exhausted local token-budget
402/quota.checked audit,quota admin update/quota.configured audit,crossing-token-
quota refusal without usage,and nested pipeline-token extraction are gone.
Production start_run/add_usage/admin quota paths remain,now without those direct
checks. Local anonymous exact auth payload assertion gone;generic legacy link
lifecycle/admin/unknown-link/cost-budget tests remain. Cloud links/runs denied;
prepaid credits/retail ledger coverage retained. None of5covers cloud behavior.
Before11passed1.90s;one-off absence red then green,no committed replacement.
After114passed0skips8.69s across auth,API,cloud,bootstrap,Supabase identity/sessions,
local refresh,control plane,retail ledger. Hosted CI/full suite unrun.
