-- Preserve the actual charge scale for new accounting; no invented backfill.
ALTER TABLE public.credit_charges ADD COLUMN units_per_credit BIGINT CHECK(units_per_credit>0);
ALTER TABLE public.operator_principals DROP CONSTRAINT operator_principals_capabilities_check;
ALTER TABLE public.operator_principals ADD CONSTRAINT operator_principals_capabilities_check
 CHECK(capabilities <@ ARRAY['analytics.read','sessions.read','debug.read','logs.read','usage.read']::text[]);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_operator_usage') THEN
  CREATE ROLE requi_operator_usage NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO requi_operator_usage;
GRANT EXECUTE ON FUNCTION public.requi_operator_read_allowed(TEXT,TEXT) TO requi_operator_usage;
GRANT SELECT(task_id,owner_id) ON public.tasks TO requi_operator_usage;
GRANT SELECT(job_id,task_id,owner_id) ON public.worker_jobs TO requi_operator_usage;
GRANT SELECT(attempt_id,job_id,owner_id,state,usage) ON public.provider_attempts TO requi_operator_usage;
GRANT SELECT(attempt_id,owner_id,state,charged_units,units_per_credit) ON public.credit_charges TO requi_operator_usage;
CREATE POLICY operator_usage_tasks ON public.tasks FOR SELECT TO requi_operator_usage USING(public.requi_operator_read_allowed('usage.read'));
CREATE POLICY operator_usage_jobs ON public.worker_jobs FOR SELECT TO requi_operator_usage USING(public.requi_operator_read_allowed('usage.read'));
CREATE POLICY operator_usage_attempts ON public.provider_attempts FOR SELECT TO requi_operator_usage USING(public.requi_operator_read_allowed('usage.read'));
CREATE POLICY operator_usage_charges ON public.credit_charges FOR SELECT TO requi_operator_usage USING(public.requi_operator_read_allowed('usage.read'));
-- No raw_usage, quote, prompts, browser grants, writes or role membership.
