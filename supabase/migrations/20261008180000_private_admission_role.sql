-- Backend-only ledger access. This role never belongs to browser authenticated.
-- Operator must provision delivery_app and grant SET membership before rollout.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_backend') THEN
  CREATE ROLE requi_backend NOLOGIN NOSUPERUSER NOBYPASSRLS INHERIT;
 END IF;
END $$;
ALTER ROLE requi_backend INHERIT;
GRANT authenticated TO requi_backend;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='delivery_app') THEN
   GRANT requi_backend TO delivery_app;
 END IF;
END $$;
REVOKE ALL ON public.admission_limits,public.provider_attempts FROM authenticated;
GRANT SELECT ON public.admission_limits TO requi_backend;
GRANT SELECT,INSERT,UPDATE ON public.provider_attempts TO requi_backend;
DROP POLICY owner_limits_read ON public.admission_limits;
CREATE POLICY owner_limits_read ON public.admission_limits FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
DROP POLICY owner_attempts ON public.provider_attempts;
CREATE POLICY owner_attempts ON public.provider_attempts TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
  SELECT 1 FROM public.worker_jobs j WHERE j.job_id=provider_attempts.job_id AND j.owner_id=provider_attempts.owner_id));
