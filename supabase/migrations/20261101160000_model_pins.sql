-- Durable reviewed public monetary configuration. New revision per edit; no customer data or secrets.
CREATE TABLE public.model_pin_revisions (
 revision text PRIMARY KEY, provider text NOT NULL CHECK(provider IN ('deepseek','ollama')),
 model text NOT NULL CHECK(length(model) BETWEEN 1 AND 200), payload jsonb NOT NULL,
 fetched_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 operator_subject uuid NOT NULL REFERENCES auth.users(id), reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 400),
 previous_revision text REFERENCES public.model_pin_revisions(revision)
);
CREATE TABLE public.model_pin_heads (
 provider text NOT NULL, model text NOT NULL, revision text NOT NULL REFERENCES public.model_pin_revisions(revision),
 is_default boolean NOT NULL DEFAULT false,
 PRIMARY KEY(provider,model)
);
CREATE UNIQUE INDEX model_pin_provider_default ON public.model_pin_heads(provider) WHERE is_default;
ALTER TABLE public.model_pin_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_pin_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.model_pin_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_pin_heads FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.model_pin_revisions,public.model_pin_heads FROM PUBLIC,authenticated;
REVOKE ALL ON public.model_pin_revisions,public.model_pin_heads FROM anon,service_role;
GRANT SELECT(revision,provider,model,payload,fetched_at) ON public.model_pin_revisions TO requi_backend;
GRANT SELECT ON public.model_pin_heads TO requi_backend;
CREATE POLICY runtime_public_pin_read ON public.model_pin_revisions FOR SELECT TO requi_backend USING(true);
CREATE POLICY runtime_public_head_read ON public.model_pin_heads FOR SELECT TO requi_backend USING(true);
-- Existing services.operate capability remains explicit in both protected config and DB registry.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='public.operator_principals'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%analytics.read%' LOOP
  EXECUTE format('ALTER TABLE public.operator_principals DROP CONSTRAINT %I',c.conname);
 END LOOP;
 ALTER TABLE public.operator_principals ADD CONSTRAINT operator_principals_capabilities_reviewed CHECK(capabilities <@ ARRAY['analytics.read','sessions.read','debug.read','logs.read','usage.read','payments.read','credits.allocate','users.suspend','messages.prepare','sessions.operate','services.operate']::text[]);
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_model_pin_editor') THEN CREATE ROLE requi_model_pin_editor NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF;
END $$;
GRANT USAGE ON SCHEMA public TO requi_model_pin_editor;
GRANT EXECUTE ON FUNCTION public.requi_operator_read_allowed(text,text) TO requi_model_pin_editor;
GRANT SELECT,INSERT ON public.model_pin_revisions TO requi_model_pin_editor;
GRANT SELECT,INSERT,UPDATE ON public.model_pin_heads TO requi_model_pin_editor;
CREATE POLICY operator_revision_read ON public.model_pin_revisions FOR SELECT TO requi_model_pin_editor USING(public.requi_operator_read_allowed('services.operate'));
CREATE POLICY operator_revision_insert ON public.model_pin_revisions FOR INSERT TO requi_model_pin_editor WITH CHECK(public.requi_operator_read_allowed('services.operate') AND operator_subject=auth.uid());
CREATE POLICY operator_head_edit ON public.model_pin_heads FOR ALL TO requi_model_pin_editor USING(public.requi_operator_read_allowed('services.operate')) WITH CHECK(public.requi_operator_read_allowed('services.operate'));
-- Membership provisioned to the restricted runtime login by the existing reviewed role grant step, not here.

-- Operator expense limits, not customer approval or activation of paid work.
CREATE TABLE public.operator_expense_policy_revisions (
 revision text PRIMARY KEY, limit_units bigint NOT NULL CHECK(limit_units>0),
 units_per_usd bigint NOT NULL CHECK(units_per_usd=1000000000000),
 operator_subject uuid NOT NULL REFERENCES auth.users(id), reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 400),
 created_at timestamptz NOT NULL DEFAULT now(),previous_revision text REFERENCES public.operator_expense_policy_revisions(revision)
);
CREATE TABLE public.operator_expense_policy_head (
 singleton boolean PRIMARY KEY CHECK(singleton), revision text NOT NULL REFERENCES public.operator_expense_policy_revisions(revision)
);
ALTER TABLE public.operator_expense_policy_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_expense_policy_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.operator_expense_policy_head ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_expense_policy_head FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_expense_policy_revisions,public.operator_expense_policy_head FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT(revision,limit_units,units_per_usd) ON public.operator_expense_policy_revisions TO requi_backend;
GRANT SELECT ON public.operator_expense_policy_head TO requi_backend;
CREATE POLICY expense_policy_runtime_read ON public.operator_expense_policy_revisions FOR SELECT TO requi_backend USING(true);
CREATE POLICY expense_policy_head_runtime_read ON public.operator_expense_policy_head FOR SELECT TO requi_backend USING(true);
GRANT SELECT,INSERT ON public.operator_expense_policy_revisions TO requi_model_pin_editor;
GRANT SELECT,INSERT,UPDATE ON public.operator_expense_policy_head TO requi_model_pin_editor;
CREATE POLICY expense_policy_operator_read ON public.operator_expense_policy_revisions FOR SELECT TO requi_model_pin_editor USING(public.requi_operator_read_allowed('services.operate'));
CREATE POLICY expense_policy_operator_insert ON public.operator_expense_policy_revisions FOR INSERT TO requi_model_pin_editor WITH CHECK(public.requi_operator_read_allowed('services.operate') AND operator_subject=auth.uid());
CREATE POLICY expense_policy_head_edit ON public.operator_expense_policy_head FOR ALL TO requi_model_pin_editor USING(public.requi_operator_read_allowed('services.operate')) WITH CHECK(public.requi_operator_read_allowed('services.operate'));
