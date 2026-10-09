-- Append-only preparation journal. Publication/install transitions stay disabled.
CREATE TABLE public.closed_revision_promotion_events (
 lineage_id TEXT NOT NULL REFERENCES public.closed_revision_lineage_plans(lineage_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 auth_session_id UUID NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence>=0),
 state TEXT NOT NULL CHECK(state IN ('prepared','staged','held_for_review')),
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[a-f0-9]{64}$'),
 previous_sha256 TEXT,
 payload TEXT NOT NULL,
 created_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(lineage_id,sequence),
 UNIQUE(lineage_id,event_sha256)
);
ALTER TABLE public.closed_revision_promotion_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.closed_revision_promotion_events FORCE ROW LEVEL SECURITY;
CREATE POLICY owned_journal_read ON public.closed_revision_promotion_events FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
CREATE POLICY owned_journal_insert ON public.closed_revision_promotion_events FOR INSERT TO requi_backend
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid()
 AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.closed_revision_lineage_plans l
  WHERE l.lineage_id=closed_revision_promotion_events.lineage_id AND l.owner_id=auth.uid()));
REVOKE ALL ON public.closed_revision_promotion_events FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON public.closed_revision_promotion_events TO requi_backend;
