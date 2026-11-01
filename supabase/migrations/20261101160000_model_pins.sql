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
