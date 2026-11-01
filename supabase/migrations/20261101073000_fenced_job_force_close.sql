-- Terminal administrative override only. Normal worker RLS stays unchanged.
CREATE FUNCTION public.requi_force_close_fenced_job(target TEXT,confirmation TEXT,review_digest TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE j public.worker_jobs; t public.tasks; s public.engine_sessions; moment DOUBLE PRECISION;
BEGIN
 IF NOT public.requi_session_active() THEN RAISE EXCEPTION 'active owner session required'; END IF;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to(auth.uid()::text,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 IF confirmation IS DISTINCT FROM 'FORCE CLOSE '||target OR review_digest !~ '^[a-f0-9]{64}$' OR review_digest IS NULL THEN RAISE EXCEPTION 'exact job confirmation and review digest required'; END IF;
 SELECT * INTO j FROM public.worker_jobs WHERE job_id=target AND owner_id=auth.uid() FOR UPDATE;
 IF NOT FOUND OR j.state NOT IN ('pending','running','paused') THEN RAISE EXCEPTION 'owned active job required'; END IF;
 SELECT * INTO t FROM public.tasks WHERE task_id=j.task_id AND owner_id=auth.uid() FOR UPDATE;
 SELECT * INTO s FROM public.engine_sessions WHERE session_id=j.session_id AND owner_id=auth.uid() AND task_id=j.task_id FOR UPDATE;
 IF t.task_id IS NULL OR s.session_id IS NULL OR t.stop_requested_at IS NULL OR t.generation<=j.generation OR s.generation<=j.generation THEN RAISE EXCEPTION 'newer task and session fence required'; END IF;
 IF EXISTS(SELECT 1 FROM public.provider_attempts WHERE job_id=target) OR EXISTS(SELECT 1 FROM public.retail_compute_segments WHERE job_id=target) THEN RAISE EXCEPTION 'legacy force repair refuses any accounting evidence'; END IF;
 moment=extract(epoch FROM statement_timestamp());
 UPDATE public.worker_jobs SET state='failed',updated_at=moment WHERE job_id=target AND owner_id=auth.uid();
 INSERT INTO public.audit_log(actor,action,task_id,owner_id,detail,ts)
 VALUES(auth.uid()::text,'worker.force_close_fenced',j.task_id,auth.uid(),jsonb_build_object(
 'job_id',target,'prior_state',j.state,'job_generation',j.generation,'binding_generation',s.generation,
 'task_generation',t.generation,'hidden',t.deleted_at IS NOT NULL,'auth_session_id',auth.jwt()->>'session_id',
 'forced_override',true,'worker_exit_verified',false,'accounting_changed',false,'plan_fingerprint',review_digest)::text,moment);
END $$;
REVOKE ALL ON FUNCTION public.requi_force_close_fenced_job(TEXT,TEXT,TEXT) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.requi_force_close_fenced_job(TEXT,TEXT,TEXT) TO requi_backend;
