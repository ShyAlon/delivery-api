-- Operator-only engine start-failure diagnostics. Delivery records the
-- sanitized block the engine sends on session-start 503s. No customer
-- surface reads this table: authenticated gets no grant at all, so exclusion
-- from customer APIs and exports is structural, not a label. The sole
-- reader is the reviewed operator role under the existing logs.read
-- capability - same operator identity, same capability registry, no new
-- audience. Presence fields are 'true'/'false'/'UNKNOWN' strings: never
-- claim values, never secrets, and an honest UNKNOWN when the failed
-- connection could not answer.
CREATE TABLE public.operator_failure_diagnostics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner_id UUID NOT NULL,
  task_id TEXT NOT NULL,
  generation INT,
  source TEXT NOT NULL,
  http_status INT NOT NULL,
  error_class TEXT NOT NULL,
  sqlstate TEXT,
  stage TEXT,
  role TEXT NOT NULL,
  sub_present TEXT NOT NULL CHECK (sub_present IN ('True','False','UNKNOWN')),
  session_id_present TEXT NOT NULL CHECK (session_id_present IN ('True','False','UNKNOWN')),
  session_active_for_sub TEXT NOT NULL CHECK (session_active_for_sub IN ('True','False','UNKNOWN')),
  message TEXT NOT NULL,
  block JSONB NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX operator_failure_diagnostics_task ON public.operator_failure_diagnostics(task_id, id DESC);

ALTER TABLE public.operator_failure_diagnostics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_failure_diagnostics FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_failure_diagnostics FROM PUBLIC, authenticated, requi_backend, requi_operator_read;
-- Supabase default ACLs grant ALL on new public tables to anon/authenticated/
-- service_role; take those back the same way the least-privilege hardening
-- does, or the table would be customer-readable through the default grants.
DO $$
DECLARE r TEXT;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
      EXECUTE format('REVOKE ALL ON public.operator_failure_diagnostics FROM %I', r);
    END IF;
  END LOOP;
END $$;
GRANT INSERT ON public.operator_failure_diagnostics TO requi_backend;
CREATE POLICY operator_failure_diagnostics_insert ON public.operator_failure_diagnostics
  FOR INSERT TO requi_backend
  WITH CHECK (owner_id = (SELECT auth.uid()));
-- INSERT ... RETURNING requires SELECT privilege: the backend may read back
-- only rows of its own tenant (owner = session claim). Customers remain fully
-- excluded; cross-tenant operator reads stay with requi_operator_read.
GRANT SELECT ON public.operator_failure_diagnostics TO requi_backend;
CREATE POLICY operator_failure_diagnostics_backend_read ON public.operator_failure_diagnostics
  FOR SELECT TO requi_backend
  USING (owner_id = (SELECT auth.uid()));
GRANT SELECT ON public.operator_failure_diagnostics TO requi_operator_read;
CREATE POLICY operator_failure_diagnostics_read ON public.operator_failure_diagnostics
  FOR SELECT TO requi_operator_read
  USING (public.requi_operator_read_allowed('logs.read'));
