-- Immutable serialized checkpoints for private revision review and staging.
-- These are evidence, never a session/lease grant or an installed execution.
CREATE TABLE public.generator_snapshots (
 snapshot_id TEXT PRIMARY KEY,
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 auth_session_id UUID NOT NULL,
 task_id TEXT NOT NULL REFERENCES public.tasks(task_id),
 generation INTEGER NOT NULL CHECK (generation > 0),
 execution_session_id TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 1 AND 80),
 checkpoint_id TEXT NOT NULL,
 encoding TEXT NOT NULL,
 payload BYTEA NOT NULL CHECK (octet_length(payload) <= 67108864),
 sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
 created_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.generator_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generator_snapshots FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.generator_snapshots FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT ON public.generator_snapshots TO requi_backend;
CREATE POLICY tenant_isolation ON public.generator_snapshots TO requi_backend
 USING (public.requi_session_active() AND owner_id=(select auth.uid())
    AND EXISTS(SELECT 1 FROM public.tasks t WHERE t.task_id=generator_snapshots.task_id AND t.owner_id=generator_snapshots.owner_id))
 WITH CHECK (public.requi_session_active() AND owner_id=(select auth.uid())
    AND auth_session_id=NULLIF(current_setting('request.jwt.claim.session_id',true),'')::uuid
    AND EXISTS(SELECT 1 FROM public.tasks t WHERE t.task_id=generator_snapshots.task_id AND t.owner_id=generator_snapshots.owner_id
        AND t.generation=generator_snapshots.generation AND t.engine_session_id=generator_snapshots.execution_session_id));
