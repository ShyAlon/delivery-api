-- Review snapshots do not confer acceptance or generation installation.
CREATE TABLE public.closed_revision_source_reviews (
 review_id TEXT PRIMARY KEY,
 lineage_id TEXT NOT NULL UNIQUE REFERENCES public.closed_revision_lineage_plans(lineage_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 auth_session_id UUID NOT NULL,
 fingerprint TEXT NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'awaiting_review' CHECK(state='awaiting_review'),
 created_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.closed_revision_source_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.closed_revision_source_reviews FORCE ROW LEVEL SECURITY;
CREATE POLICY owned_source_review_read ON public.closed_revision_source_reviews FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
CREATE POLICY owned_source_review_insert ON public.closed_revision_source_reviews FOR INSERT TO requi_backend
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid()
 AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.closed_revision_lineage_plans l JOIN public.tasks t ON t.task_id=l.task_id
  WHERE l.lineage_id=closed_revision_source_reviews.lineage_id AND l.owner_id=auth.uid()
   AND t.owner_id=auth.uid() AND t.generation=l.parent_generation AND t.engine_session_id=l.parent_execution_session_id));
REVOKE ALL ON public.closed_revision_source_reviews FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON public.closed_revision_source_reviews TO requi_backend;
