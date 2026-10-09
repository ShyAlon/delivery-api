-- Backend-only attempt evidence, same owner/session RLS as provider_attempts.
-- No browser grant, no migration of guessed historical data.
ALTER TABLE public.provider_attempts ADD COLUMN raw_usage JSONB;
ALTER TABLE public.provider_attempts ADD COLUMN raw_usage_received_at DOUBLE PRECISION;
