-- Durable owner/task/generation approval for attended paid previews only.
-- Justification: approval must survive child restarts and serialize concurrent
-- dispatch across processes. Config or process memory cannot enforce that.
CREATE TABLE public.preview_spend_approvals (
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 task_id TEXT NOT NULL REFERENCES public.tasks(task_id),
 generation INT NOT NULL,
 auth_session_id UUID NOT NULL,
 provider TEXT NOT NULL CHECK(provider='deepseek'),
 model TEXT NOT NULL CHECK(length(model)>0),
 limit_units BIGINT NOT NULL CHECK(limit_units>0),
 units_per_usd BIGINT NOT NULL CHECK(units_per_usd>0),
 approved_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(owner_id,task_id,generation)
);
ALTER TABLE public.preview_spend_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.preview_spend_approvals FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.preview_spend_approvals FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT ON public.preview_spend_approvals TO requi_backend;
CREATE POLICY preview_spend_owner ON public.preview_spend_approvals TO requi_backend
 USING(owner_id=auth.uid() AND public.requi_session_active())
 WITH CHECK(owner_id=auth.uid() AND public.requi_session_active()
 AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid);
