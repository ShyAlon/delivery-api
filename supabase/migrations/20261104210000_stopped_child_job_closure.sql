-- Terminal closure only for the original authenticated owner's stopped worker.
-- No INSERT policy, no generation rewrite, no restart, no browser grant.
-- Existing same-generation policy and execution fences remain unchanged.
CREATE POLICY backend_stopped_worker_close ON public.worker_jobs
 FOR UPDATE TO requi_backend
 USING (public.requi_session_active() AND owner_id=auth.uid()
   AND auth_session_id::text=current_setting('request.jwt.claim.session_id',true)
   AND state='running' AND EXISTS (
     SELECT 1 FROM public.engine_sessions s JOIN public.tasks t ON t.task_id=s.task_id
     WHERE s.session_id=worker_jobs.session_id AND s.owner_id=worker_jobs.owner_id
       AND s.task_id=worker_jobs.task_id AND s.generation=worker_jobs.generation+1
       AND s.control->>'stopped'='true' AND t.stop_requested_at IS NOT NULL))
 WITH CHECK (public.requi_session_active() AND owner_id=auth.uid()
   AND auth_session_id::text=current_setting('request.jwt.claim.session_id',true)
   AND state='done' AND EXISTS (
     SELECT 1 FROM public.engine_sessions s JOIN public.tasks t ON t.task_id=s.task_id
     WHERE s.session_id=worker_jobs.session_id AND s.owner_id=worker_jobs.owner_id
       AND s.task_id=worker_jobs.task_id AND s.generation=worker_jobs.generation+1
       AND s.control->>'stopped'='true' AND t.stop_requested_at IS NOT NULL));

-- WITH CHECK sees only NEW. Prevent changing provenance alongside closure,
-- including rewriting the old generation to exploit the existing policy.
CREATE FUNCTION public.requi_stopped_worker_immutable() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.engine_sessions s WHERE s.session_id=OLD.session_id
   AND s.owner_id=OLD.owner_id AND s.generation<>OLD.generation) THEN
   IF OLD.state<>'running' OR NEW.state<>'done'
      OR (to_jsonb(NEW)-'state'-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'state'-'updated_at') THEN
     RAISE EXCEPTION 'stopped worker permits terminal closure only' USING ERRCODE='42501';
   END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.requi_stopped_worker_immutable() FROM PUBLIC;
CREATE TRIGGER stopped_worker_immutable BEFORE UPDATE ON public.worker_jobs
 FOR EACH ROW EXECUTE FUNCTION public.requi_stopped_worker_immutable();
