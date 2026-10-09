-- Private authenticated decision receipt, not an HTTP route or browser grant.
CREATE TABLE public.closed_revision_acceptances (
 acceptance_id TEXT PRIMARY KEY,
 lineage_id TEXT NOT NULL REFERENCES public.closed_revision_lineage_plans(lineage_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),auth_session_id UUID NOT NULL,
 task_id TEXT NOT NULL,review_sha256 TEXT NOT NULL CHECK(review_sha256 ~ '^[a-f0-9]{64}$'),
 decision TEXT NOT NULL CHECK(decision='approve'),extra_cost_acknowledged BOOLEAN NOT NULL CHECK(extra_cost_acknowledged),
 payload TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,
 FOREIGN KEY(owner_id,task_id) REFERENCES public.tasks(owner_id,task_id),
 UNIQUE(lineage_id,review_sha256,auth_session_id)
);
CREATE TABLE public.closed_revision_acceptance_revocations (
 acceptance_id TEXT PRIMARY KEY REFERENCES public.closed_revision_acceptances(acceptance_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),auth_session_id UUID NOT NULL,
 created_at DOUBLE PRECISION NOT NULL
);
DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['closed_revision_acceptances','closed_revision_acceptance_revocations'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY owner_read ON public.%I FOR SELECT TO requi_backend USING(public.requi_session_active() AND owner_id=auth.uid())',tab);
  EXECUTE format('CREATE POLICY owner_insert ON public.%I FOR INSERT TO requi_backend WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND auth_session_id=nullif(current_setting(''request.jwt.claim.session_id'',true),'''')::uuid)',tab);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,authenticated',tab);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO requi_backend',tab);
 END LOOP;
END $$;
