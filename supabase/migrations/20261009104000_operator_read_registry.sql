-- Separate least-privilege read role. No BYPASSRLS, no membership provisioning.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_operator_read') THEN
  CREATE ROLE requi_operator_read NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
CREATE TABLE public.operator_principals (
 subject UUID PRIMARY KEY REFERENCES auth.users(id), registry_version TEXT NOT NULL,
 capabilities TEXT[] NOT NULL, enabled BOOLEAN NOT NULL DEFAULT false,
 CHECK(length(registry_version)>0),
 CHECK(capabilities <@ ARRAY['analytics.read','sessions.read','debug.read','logs.read']::text[])
);
CREATE UNIQUE INDEX sole_enabled_operator ON public.operator_principals(enabled) WHERE enabled;
ALTER TABLE public.operator_principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_principals FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_principals FROM PUBLIC,authenticated,requi_backend,requi_operator_read;
CREATE FUNCTION public.requi_operator_read_allowed(capability TEXT, expected_version TEXT DEFAULT NULL) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
 SELECT public.requi_session_active() AND EXISTS(
  SELECT 1 FROM public.operator_principals p WHERE p.subject=auth.uid()
   AND p.enabled AND capability=ANY(p.capabilities)
   AND (expected_version IS NULL OR p.registry_version=expected_version)
 );
$$;
REVOKE ALL ON FUNCTION public.requi_operator_read_allowed(TEXT,TEXT) FROM PUBLIC,authenticated;
GRANT USAGE ON SCHEMA public TO requi_operator_read;
GRANT EXECUTE ON FUNCTION public.requi_operator_read_allowed(TEXT,TEXT) TO requi_operator_read;
GRANT SELECT ON public.users,public.tasks,public.engine_sessions,public.worker_jobs,public.credit_accounts,public.provider_attempts TO requi_operator_read;
CREATE POLICY operator_users_read ON public.users FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('sessions.read'));
CREATE POLICY operator_tasks_read ON public.tasks FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('sessions.read'));
CREATE POLICY operator_sessions_read ON public.engine_sessions FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('sessions.read'));
CREATE POLICY operator_jobs_read ON public.worker_jobs FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('sessions.read'));
CREATE POLICY operator_credits_read ON public.credit_accounts FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('analytics.read'));
CREATE POLICY operator_expense_read ON public.provider_attempts FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('analytics.read'));
-- No INSERT/UPDATE/DELETE, admin operation grants or default principal enabled.
