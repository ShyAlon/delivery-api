-- Exact retail evidence. Browser clients receive no monetary/operator table grant.
ALTER TABLE public.credit_accounts ADD COLUMN fractional_debit_units NUMERIC NOT NULL DEFAULT 0
 CHECK(fractional_debit_units>=0 AND fractional_debit_units<1);
CREATE TABLE public.retail_contributions (
 contribution_key TEXT PRIMARY KEY CHECK(length(contribution_key) BETWEEN 1 AND 240),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 job_id TEXT NOT NULL REFERENCES public.worker_jobs(job_id),
 phase_key TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('tokens','compute')),
 input_tokens BIGINT NOT NULL CHECK(input_tokens>=0),
 output_tokens BIGINT NOT NULL CHECK(output_tokens>=0),
 deterministic_ms NUMERIC NOT NULL CHECK(deterministic_ms>=0 AND deterministic_ms<'Infinity'::numeric),
 rates JSONB NOT NULL,
 exact_units NUMERIC NOT NULL CHECK(exact_units>=0 AND exact_units<'Infinity'::numeric),
 posted_units BIGINT NOT NULL CHECK(posted_units>=0),
 units_per_credit BIGINT NOT NULL CHECK(units_per_credit=1000000000),
 fingerprint TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.retail_contributions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retail_contributions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_retail_contributions ON public.retail_contributions TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
 SELECT 1 FROM public.worker_jobs w WHERE w.job_id=retail_contributions.job_id AND w.owner_id=retail_contributions.owner_id));
REVOKE ALL ON public.retail_contributions FROM PUBLIC,authenticated;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.retail_contributions FROM anon;
 END IF;
END $$;
GRANT SELECT,INSERT ON public.retail_contributions TO requi_backend;
-- Preserve immutable payment intents, historic attempt quotes and charge scales.
-- Active account scale conversion is explicit and independently evidenced.
CREATE TABLE public.retail_scale_migrations (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 old_scale BIGINT NOT NULL, old_balance BIGINT NOT NULL,
 new_scale BIGINT NOT NULL CHECK(new_scale=1000000000), new_balance BIGINT NOT NULL,
 migrated_at DOUBLE PRECISION NOT NULL,
 CHECK(new_balance::numeric*old_scale::numeric=old_balance::numeric*new_scale::numeric)
);
ALTER TABLE public.retail_scale_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retail_scale_migrations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.retail_scale_migrations FROM PUBLIC,authenticated,requi_backend;
-- No automatic rewrite or migration of a live/pending account.
CREATE TABLE public.retail_compute_segments (
 segment_key TEXT PRIMARY KEY, owner_id UUID NOT NULL REFERENCES auth.users(id),
 job_id TEXT NOT NULL REFERENCES public.worker_jobs(job_id), phase_key TEXT NOT NULL,
 rates JSONB NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','settled','unresolved')),
 deterministic_ms NUMERIC, started_at DOUBLE PRECISION NOT NULL, settled_at DOUBLE PRECISION
);
ALTER TABLE public.retail_compute_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retail_compute_segments FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_compute_segments ON public.retail_compute_segments TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
 SELECT 1 FROM public.worker_jobs w WHERE w.job_id=retail_compute_segments.job_id AND w.owner_id=retail_compute_segments.owner_id));
REVOKE ALL ON public.retail_compute_segments FROM PUBLIC,authenticated;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
 REVOKE ALL ON public.retail_compute_segments FROM anon; END IF; END $$;
GRANT SELECT,INSERT,UPDATE ON public.retail_compute_segments TO requi_backend;
ALTER TABLE public.provider_attempts ADD COLUMN provider_started_at DOUBLE PRECISION;
ALTER TABLE public.provider_attempts ADD COLUMN billed_rate_evidence JSONB;
ALTER TABLE public.provider_attempts ADD COLUMN input_estimate_overrun BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.provider_attempts ADD COLUMN provider_balance_evidence JSONB;
