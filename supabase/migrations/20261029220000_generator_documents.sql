-- Mutable workflow documents. Exact-byte durable state, never path authority.
CREATE TABLE public.generator_documents (
 execution_session_id TEXT NOT NULL REFERENCES public.engine_sessions(session_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id), auth_session_id UUID NOT NULL,
 generation INTEGER NOT NULL CHECK(generation>0),
 document_key TEXT NOT NULL CHECK(length(document_key) BETWEEN 1 AND 500),
 payload BYTEA CHECK(octet_length(payload)<=67108864),
 sha256 TEXT CHECK(sha256 ~ '^[a-f0-9]{64}$'), updated_at DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(execution_session_id,document_key),
 CHECK((payload IS NULL)=(sha256 IS NULL))
);
ALTER TABLE public.generator_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generator_documents FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.generator_documents FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.generator_documents TO requi_backend;
CREATE POLICY tenant_isolation ON public.generator_documents TO requi_backend
 USING(public.requi_session_active() AND owner_id=(select auth.uid())
   AND EXISTS(SELECT 1 FROM engine_sessions s WHERE s.session_id=execution_session_id AND s.owner_id=generator_documents.owner_id AND s.generation=generator_documents.generation))
 WITH CHECK(public.requi_session_active() AND owner_id=(select auth.uid())
   AND auth_session_id=NULLIF(current_setting('request.jwt.claim.session_id',true),'')::uuid
   AND EXISTS(SELECT 1 FROM engine_sessions s WHERE s.session_id=execution_session_id AND s.owner_id=generator_documents.owner_id AND s.generation=generator_documents.generation));
