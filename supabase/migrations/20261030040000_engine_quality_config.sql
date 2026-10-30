-- Service-global question quality configuration, no historical file import.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_engine_config_read') THEN
  CREATE ROLE requi_engine_config_read NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
CREATE TABLE public.engine_quality_config (
 config_key text PRIMARY KEY CHECK(config_key='question-quality'),
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=65536),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.engine_quality_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.engine_quality_config FORCE ROW LEVEL SECURITY;
DO $$ DECLARE r text; BEGIN
 FOREACH r IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend','requi_operator_read'] LOOP
  IF r='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON public.engine_quality_config FROM %s',CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
  END IF;
 END LOOP;
END $$;
GRANT USAGE ON SCHEMA public TO requi_engine_config_read;
GRANT SELECT ON public.engine_quality_config TO requi_engine_config_read;
CREATE POLICY engine_config_runtime_read ON public.engine_quality_config FOR SELECT TO requi_engine_config_read USING(config_key='question-quality');
-- No runtime writer. The existing customer/API fence remains unchanged.
