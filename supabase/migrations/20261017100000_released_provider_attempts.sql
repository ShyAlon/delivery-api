-- A provider attempt can be released when the provider provably never received the request (a connect failure
-- before any request byte). Released attempts bill 0. Safe to run twice: the post-restore step of a resume
-- re-applies this file to a database restored from an older dump.
ALTER TABLE public.provider_attempts DROP CONSTRAINT IF EXISTS provider_attempts_state_check;
ALTER TABLE public.provider_attempts ADD CONSTRAINT provider_attempts_state_check
  CHECK (state IN ('reserved','dispatched','settled','cancelled','uncertain','failure_waived','released'));
ALTER TABLE public.provider_attempts DROP CONSTRAINT IF EXISTS provider_attempts_released_unbilled;
ALTER TABLE public.provider_attempts ADD CONSTRAINT provider_attempts_released_unbilled
  CHECK (state <> 'released' OR (raw_usage IS NULL AND usage IS NULL AND expense_actual IS NULL AND retail_actual IS NULL AND settled_at IS NULL));
ALTER TABLE public.credit_charges DROP CONSTRAINT IF EXISTS credit_charges_state_check;
ALTER TABLE public.credit_charges ADD CONSTRAINT credit_charges_state_check
  CHECK (state IN ('pending','settled','failure_waived','released'));
ALTER TABLE public.credit_charges DROP CONSTRAINT IF EXISTS credit_charges_released_no_debit;
ALTER TABLE public.credit_charges ADD CONSTRAINT credit_charges_released_no_debit
  CHECK (state <> 'released' OR (charged_units IS NULL AND settled_at IS NULL));
