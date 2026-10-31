-- Immutable owner/task/generation/revision-bound final exports. No public access.
CREATE TABLE public.final_packages (
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 task_id TEXT NOT NULL,
 generation INTEGER NOT NULL CHECK(generation>0),
 package_id TEXT NOT NULL CHECK(package_id ~ '^[a-f0-9]{64}$'),
 execution_session_id TEXT NOT NULL REFERENCES engine_sessions(session_id),
 revision_id TEXT NOT NULL, revision_hash TEXT NOT NULL,
 payload BYTEA NOT NULL CHECK(octet_length(payload)<=24000000),
 sha256 TEXT NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 created_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(owner_id,task_id,generation,package_id),
 FOREIGN KEY(owner_id,task_id) REFERENCES tasks(owner_id,task_id)
);
ALTER TABLE public.final_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.final_packages FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_final_packages ON public.final_packages TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(SELECT 1 FROM tasks t WHERE t.task_id=final_packages.task_id AND t.owner_id=final_packages.owner_id AND t.deleted_at IS NULL))
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(SELECT 1 FROM engine_sessions s WHERE s.session_id=final_packages.execution_session_id AND s.owner_id=final_packages.owner_id AND s.task_id=final_packages.task_id AND s.generation=final_packages.generation));
REVOKE ALL ON public.final_packages FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT ON public.final_packages TO requi_backend;
