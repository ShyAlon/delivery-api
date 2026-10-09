-- Immutable held lineage plans, not installed engine session bindings.
CREATE TABLE public.closed_revision_lineage_plans (
 lineage_id TEXT PRIMARY KEY,
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 auth_session_id UUID NOT NULL,
 intent_id TEXT NOT NULL UNIQUE REFERENCES public.closed_revision_restart_intents(intent_id),
 task_id TEXT NOT NULL,
 parent_execution_session_id TEXT NOT NULL REFERENCES public.engine_sessions(session_id),
 parent_generation INTEGER NOT NULL CHECK(parent_generation>0),
 execution_session_id TEXT NOT NULL UNIQUE,
 generation INTEGER NOT NULL CHECK(generation=parent_generation+1),
 semantic_session_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'planned' CHECK(state='planned'),
 created_at DOUBLE PRECISION NOT NULL,
 CHECK(execution_session_id<>parent_execution_session_id AND execution_session_id<>semantic_session_id),
 FOREIGN KEY(owner_id,task_id) REFERENCES public.tasks(owner_id,task_id)
);
ALTER TABLE public.closed_revision_lineage_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.closed_revision_lineage_plans FORCE ROW LEVEL SECURITY;
CREATE POLICY owned_lineage_read ON public.closed_revision_lineage_plans FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
CREATE POLICY owned_lineage_insert ON public.closed_revision_lineage_plans FOR INSERT TO requi_backend
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid()
 AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.closed_revision_restart_intents i
  WHERE i.intent_id=closed_revision_lineage_plans.intent_id AND i.owner_id=auth.uid()
   AND i.task_id=closed_revision_lineage_plans.task_id AND i.state='held'
   AND i.source_generation=closed_revision_lineage_plans.parent_generation)
 AND EXISTS(SELECT 1 FROM public.tasks t JOIN public.engine_sessions s ON s.session_id=t.engine_session_id
  WHERE t.task_id=closed_revision_lineage_plans.task_id AND t.owner_id=auth.uid()
   AND t.generation=closed_revision_lineage_plans.parent_generation
   AND s.session_id=closed_revision_lineage_plans.parent_execution_session_id
   AND s.task_id=t.task_id AND s.owner_id=t.owner_id AND s.generation=t.generation));
REVOKE ALL ON public.closed_revision_lineage_plans FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON public.closed_revision_lineage_plans TO requi_backend;
