-- Explicit private audited unbilled failure. Unknown usage/expense stay NULL.
ALTER TABLE public.provider_attempts DROP CONSTRAINT provider_attempts_state_check;
ALTER TABLE public.provider_attempts ADD CONSTRAINT provider_attempts_state_check
 CHECK(state IN ('reserved','dispatched','settled','cancelled','uncertain','failure_waived'));
ALTER TABLE public.provider_attempts ADD CONSTRAINT provider_attempts_failure_waived_unknown
 CHECK(state <> 'failure_waived' OR (raw_usage IS NULL AND usage IS NULL AND expense_actual IS NULL AND retail_actual IS NULL AND settled_at IS NULL));
ALTER TABLE public.credit_charges DROP CONSTRAINT credit_charges_state_check;
ALTER TABLE public.credit_charges ADD CONSTRAINT credit_charges_state_check CHECK(state IN ('pending','settled','failure_waived'));
ALTER TABLE public.credit_charges ADD CONSTRAINT credit_charges_failure_waived_no_debit
 CHECK(state <> 'failure_waived' OR (charged_units IS NULL AND settled_at IS NULL));
