-- Derived JSONL projection. No direct browser access or debit authority.
CREATE TABLE public.document_projections (
 execution_session_id TEXT NOT NULL,document_key TEXT NOT NULL,owner_id UUID NOT NULL,
 generation INT NOT NULL,body JSONB NOT NULL,
 PRIMARY KEY(execution_session_id,document_key),
 FOREIGN KEY(execution_session_id,document_key) REFERENCES public.generator_documents(execution_session_id,document_key) ON DELETE CASCADE
);
ALTER TABLE public.document_projections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_projections FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_usage_projection ON public.document_projections TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(SELECT 1 FROM public.engine_sessions e WHERE e.session_id=execution_session_id AND e.owner_id=document_projections.owner_id AND e.generation=document_projections.generation))
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(SELECT 1 FROM public.engine_sessions e WHERE e.session_id=execution_session_id AND e.owner_id=document_projections.owner_id AND e.generation=document_projections.generation));
REVOKE ALL ON public.document_projections FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.document_projections TO requi_backend;
