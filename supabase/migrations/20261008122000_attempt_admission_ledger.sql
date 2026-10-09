-- Operator-provisioned reviewed limits only. No default allowance/price.
CREATE TABLE public.admission_limits (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 config_version TEXT NOT NULL, period_seconds BIGINT NOT NULL CHECK(period_seconds>0),
 expense_limit BIGINT NOT NULL CHECK(expense_limit>=0), retail_limit BIGINT NOT NULL CHECK(retail_limit>=0),
 attempt_expense_max BIGINT NOT NULL CHECK(attempt_expense_max>0), attempt_retail_max BIGINT NOT NULL CHECK(attempt_retail_max>0),
 rate_window_seconds BIGINT NOT NULL CHECK(rate_window_seconds>0), rate_max BIGINT NOT NULL CHECK(rate_max>0),
 concurrency_max BIGINT NOT NULL CHECK(concurrency_max>0)
);
ALTER TABLE public.admission_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admission_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_limits_read ON public.admission_limits FOR SELECT TO authenticated
 USING (public.requi_session_active() AND owner_id=auth.uid());
GRANT SELECT ON public.admission_limits TO authenticated;
REVOKE INSERT,UPDATE,DELETE ON public.admission_limits FROM authenticated;
CREATE TABLE public.provider_attempts (
 attempt_id TEXT PRIMARY KEY, owner_id UUID NOT NULL REFERENCES auth.users(id),
 job_id TEXT NOT NULL REFERENCES public.worker_jobs(job_id),
 config_version TEXT NOT NULL, quote JSONB NOT NULL, fingerprint TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','dispatched','settled','cancelled','uncertain')),
 expense_reserved BIGINT NOT NULL CHECK(expense_reserved>=0), retail_reserved BIGINT NOT NULL CHECK(retail_reserved>=0),
 expense_actual BIGINT, retail_actual BIGINT, usage JSONB,
 created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX provider_attempts_owner_time ON public.provider_attempts(owner_id,created_at);
ALTER TABLE public.provider_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_attempts ON public.provider_attempts TO authenticated
 USING (public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK (public.requi_session_active() AND owner_id=auth.uid() AND EXISTS (
   SELECT 1 FROM public.worker_jobs j WHERE j.job_id=provider_attempts.job_id AND j.owner_id=provider_attempts.owner_id));
GRANT SELECT,INSERT,UPDATE ON public.provider_attempts TO authenticated;
-- Existing platform default grants must not expose provisioning to anon.
REVOKE ALL ON public.admission_limits,public.provider_attempts FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
   REVOKE ALL ON public.admission_limits,public.provider_attempts FROM anon;
 END IF;
END $$;
