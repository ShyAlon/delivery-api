-- Private installation records/journal. No acceptance route or browser grant.
CREATE TABLE public.closed_revision_installations (
 installation_id TEXT PRIMARY KEY,
 lineage_id TEXT NOT NULL UNIQUE REFERENCES public.closed_revision_lineage_plans(lineage_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id), auth_session_id UUID NOT NULL,
 task_id TEXT NOT NULL, review_sha256 TEXT NOT NULL CHECK(review_sha256 ~ '^[a-f0-9]{64}$'),
 plan_sha256 TEXT NOT NULL CHECK(plan_sha256 ~ '^[a-f0-9]{64}$'),
 acceptance_reference TEXT NOT NULL, payload TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
 FOREIGN KEY(owner_id,task_id) REFERENCES public.tasks(owner_id,task_id)
);
CREATE TABLE public.closed_revision_install_events (
 installation_id TEXT NOT NULL REFERENCES public.closed_revision_installations(installation_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id), auth_session_id UUID NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence>=0),
 state TEXT NOT NULL CHECK(state IN ('authorized','promotion_pending','files_promoted','install_pending','installed','held_for_review')),
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[a-f0-9]{64}$'), previous_sha256 TEXT,
 payload TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(installation_id,sequence), UNIQUE(installation_id,event_sha256)
);
CREATE TABLE public.installed_execution_bindings (
 session_id TEXT PRIMARY KEY REFERENCES public.engine_sessions(session_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id), auth_session_id UUID NOT NULL,
 task_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>=2),
 semantic_session_id TEXT NOT NULL, installation_id TEXT NOT NULL UNIQUE REFERENCES public.closed_revision_installations(installation_id),
 lineage_sha256 TEXT NOT NULL CHECK(lineage_sha256 ~ '^[a-f0-9]{64}$'),
 anchor_sha256 TEXT NOT NULL CHECK(anchor_sha256 ~ '^[a-f0-9]{64}$'), created_at DOUBLE PRECISION NOT NULL,
 FOREIGN KEY(owner_id,task_id) REFERENCES public.tasks(owner_id,task_id),
 CHECK(session_id<>semantic_session_id)
);
DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['closed_revision_installations','closed_revision_install_events','installed_execution_bindings'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY owner_read ON public.%I FOR SELECT TO requi_backend USING(public.requi_session_active() AND owner_id=auth.uid())',tab);
  EXECUTE format('CREATE POLICY owner_insert ON public.%I FOR INSERT TO requi_backend WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND auth_session_id=nullif(current_setting(''request.jwt.claim.session_id'',true),'''')::uuid)',tab);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,authenticated',tab);
  EXECUTE format('GRANT SELECT,INSERT ON public.%I TO requi_backend',tab);
 END LOOP;
END $$;
