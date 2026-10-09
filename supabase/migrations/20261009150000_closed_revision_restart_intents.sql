-- Held, immutable reviewed restart intent. No dispatch or generation mutation.
CREATE TABLE public.closed_revision_restart_intents (
 intent_id TEXT PRIMARY KEY,
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 1 AND 200),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 auth_session_id UUID NOT NULL,
 task_id TEXT NOT NULL,
 source_generation INTEGER NOT NULL CHECK(source_generation>0),
 gate_key TEXT NOT NULL CHECK(gate_key IN ('gate-srs','gate-architecture')),
 fingerprint TEXT NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'held' CHECK(state='held'),
 created_at DOUBLE PRECISION NOT NULL,
 UNIQUE(owner_id,request_key),
 FOREIGN KEY(owner_id,task_id) REFERENCES tasks(owner_id,task_id)
);
ALTER TABLE public.closed_revision_restart_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.closed_revision_restart_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY owned_intent_read ON public.closed_revision_restart_intents FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
CREATE POLICY owned_intent_insert ON public.closed_revision_restart_intents FOR INSERT TO requi_backend
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid()
  AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid
  AND EXISTS(SELECT 1 FROM public.tasks t WHERE t.task_id=closed_revision_restart_intents.task_id
   AND t.owner_id=auth.uid() AND t.generation=closed_revision_restart_intents.source_generation));
REVOKE ALL ON public.closed_revision_restart_intents FROM PUBLIC,authenticated;
GRANT SELECT,INSERT ON public.closed_revision_restart_intents TO requi_backend;
