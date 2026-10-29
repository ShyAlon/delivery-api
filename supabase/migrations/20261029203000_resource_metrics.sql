-- Additive metrics foundation only. No sampler activation or historical import.
-- A trusted process writer has no tenant/task/credit access through this role.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_metrics_writer') THEN
  CREATE ROLE requi_metrics_writer NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO requi_metrics_writer;
CREATE TABLE public.resource_run_samples (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source_id TEXT NOT NULL,
 ts DOUBLE PRECISION NOT NULL, run_key TEXT NOT NULL, task_id TEXT NOT NULL,
 session_id TEXT, phase TEXT, pid INT, procs INT, rss_mb DOUBLE PRECISION,
 peak_rss_mb DOUBLE PRECISION, cpu_seconds DOUBLE PRECISION, cpu_pct DOUBLE PRECISION
);
CREATE INDEX resource_run_samples_source_run ON public.resource_run_samples(source_id,run_key,ts);
CREATE INDEX resource_run_samples_task ON public.resource_run_samples(task_id,ts);
CREATE TABLE public.resource_run_summaries (
 source_id TEXT NOT NULL, run_key TEXT NOT NULL, task_id TEXT NOT NULL,
 session_id TEXT, phase TEXT, pid INT, started_at DOUBLE PRECISION, ended_at DOUBLE PRECISION,
 samples BIGINT, avg_cpu_pct DOUBLE PRECISION, max_cpu_pct DOUBLE PRECISION,
 avg_rss_mb DOUBLE PRECISION, max_rss_mb DOUBLE PRECISION, cpu_seconds DOUBLE PRECISION,
 rolled_up_at DOUBLE PRECISION, PRIMARY KEY(source_id,run_key)
);
CREATE TABLE public.resource_container_samples (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source_id TEXT NOT NULL,
 ts DOUBLE PRECISION NOT NULL, mem_used_mb DOUBLE PRECISION, mem_limit_mb DOUBLE PRECISION,
 cpu_seconds DOUBLE PRECISION, cpu_pct DOUBLE PRECISION
);
CREATE INDEX resource_container_samples_source ON public.resource_container_samples(source_id,ts);
CREATE TABLE public.resource_container_summaries (
 source_id TEXT NOT NULL, day TEXT NOT NULL, samples BIGINT,
 avg_mem_mb DOUBLE PRECISION, max_mem_mb DOUBLE PRECISION,
 avg_cpu_pct DOUBLE PRECISION, max_cpu_pct DOUBLE PRECISION,
 rolled_up_at DOUBLE PRECISION, PRIMARY KEY(source_id,day)
);
DO $$
DECLARE t TEXT; r TEXT;
BEGIN
 FOREACH t IN ARRAY ARRAY['resource_run_samples','resource_run_summaries','resource_container_samples','resource_container_summaries'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  FOREACH r IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend'] LOOP
   IF r='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
    EXECUTE format('REVOKE ALL ON public.%I FROM %s',t,CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
   END IF;
  END LOOP;
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO requi_metrics_writer',t);
  EXECUTE format('CREATE POLICY metrics_writer ON public.%I TO requi_metrics_writer USING (source_id = current_setting(''requi.metrics_source'',true)) WITH CHECK (source_id = current_setting(''requi.metrics_source'',true))',t);
  EXECUTE format('GRANT SELECT ON public.%I TO requi_operator_read',t);
  EXECUTE format('CREATE POLICY metrics_operator ON public.%I FOR SELECT TO requi_operator_read USING (public.requi_operator_read_allowed(''logs.read''))',t);
 END LOOP;
END $$;
-- Identity sequences may inherit Supabase default ACLs independently of tables.
DO $$
DECLARE s TEXT; r TEXT;
BEGIN
 FOREACH s IN ARRAY ARRAY['resource_run_samples_id_seq','resource_container_samples_id_seq'] LOOP
  FOREACH r IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend'] LOOP
   IF r='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
    EXECUTE format('REVOKE ALL ON SEQUENCE public.%I FROM %s',s,CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
   END IF;
  END LOOP;
 END LOOP;
END $$;
