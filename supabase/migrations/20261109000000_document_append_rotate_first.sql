-- A resumed interview under a NEW sign-in must be able to append. The chunk
-- policy (tenant_chunks WITH CHECK) demands auth_session_id == the current
-- claim, but the function rotated auth_session_id only AFTER the chunk insert,
-- so every append to a document created under an earlier auth session died
-- 42501 (2026-11-09: an overnight-resumed interview rejected every answer,
-- five engine respawns, before any model call). Rotate first: the row is
-- locked FOR UPDATE and the append is one transaction, so a later failure
-- rolls the rotation back with it. The fence is unchanged - only the current
-- claim holder can rotate (the rotation UPDATE passes its own WITH CHECK only
-- when the new auth_session_id equals the claim).
CREATE OR REPLACE FUNCTION public.requi_document_append(sid TEXT,gen INT,key TEXT,raw BYTEA,owner UUID,auth_session UUID,stamp DOUBLE PRECISION) RETURNS void
LANGUAGE plpgsql SET search_path='' AS $$
DECLARE d public.generator_documents;
BEGIN
 INSERT INTO public.generator_documents(execution_session_id,owner_id,auth_session_id,generation,document_key,payload,sha256,updated_at)
 VALUES(sid,owner,auth_session,gen,key,''::bytea,encode(sha256(''::bytea),'hex'),stamp) ON CONFLICT DO NOTHING;
 SELECT * INTO d FROM public.generator_documents WHERE execution_session_id=sid AND document_key=key AND generation=gen FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'document generation unavailable'; END IF;
 IF raw IS NULL OR octet_length(COALESCE(d.payload,''::bytea))+d.chunk_bytes+octet_length(raw)>67108864 THEN RAISE EXCEPTION 'bounded document bytes required'; END IF;
 IF d.auth_session_id IS DISTINCT FROM auth_session THEN
  UPDATE public.generator_documents SET auth_session_id=auth_session WHERE execution_session_id=sid AND document_key=key;
 END IF;
 INSERT INTO public.document_chunks VALUES(sid,key,d.chunk_epoch,d.chunk_count+1,raw,encode(sha256(raw),'hex'));
 UPDATE public.generator_documents SET chunk_count=d.chunk_count+1,chunk_bytes=d.chunk_bytes+octet_length(raw),updated_at=stamp WHERE execution_session_id=sid AND document_key=key;
END $$;
