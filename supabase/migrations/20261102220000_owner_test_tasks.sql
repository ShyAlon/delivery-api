-- Owner applies this migration. No default test owner, no broad DELETE grant.
ALTER TABLE public.tasks ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.tasks ADD COLUMN test_purged_at DOUBLE PRECISION;
CREATE FUNCTION public.requi_test_task_owner() RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path='' AS $$
 SELECT public.requi_session_active() AND EXISTS(SELECT 1 FROM public.operator_principals
 WHERE subject=auth.uid() AND enabled AND 'sessions.operate'=ANY(capabilities));
$$;
REVOKE ALL ON FUNCTION public.requi_test_task_owner() FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.requi_test_task_owner() TO requi_backend;
CREATE FUNCTION public.requi_test_task_marker_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.is_test IS DISTINCT FROM OLD.is_test OR (OLD.is_test AND NEW.owner_id IS DISTINCT FROM OLD.owner_id)) THEN
  RAISE EXCEPTION 'test marker is immutable';
 END IF;
 IF TG_OP='INSERT' AND NEW.is_test AND NOT public.requi_test_task_owner() THEN
  RAISE EXCEPTION 'reviewed owner required for test task';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER requi_test_task_marker_guard BEFORE INSERT OR UPDATE ON public.tasks
 FOR EACH ROW EXECUTE FUNCTION public.requi_test_task_marker_guard();
REVOKE ALL ON FUNCTION public.requi_test_task_marker_guard() FROM PUBLIC,anon,authenticated,service_role,requi_backend;

-- Successful child acceptance only. Retain task/session/job identity tombstones,
-- provider attempts, retail/credit/expense evidence and audit. No ledger updates.
CREATE FUNCTION public.requi_purge_test_task(target_task TEXT,confirmed BOOLEAN) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE t public.tasks; sessions TEXT[]; threads TEXT[]; hashes TEXT[]; moment DOUBLE PRECISION;
BEGIN
 IF confirmed IS DISTINCT FROM true OR NOT public.requi_test_task_owner() THEN
  RAISE EXCEPTION 'confirmed reviewed owner required'; END IF;
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 SELECT * INTO t FROM public.tasks WHERE task_id=target_task AND owner_id=auth.uid() FOR UPDATE;
 IF NOT FOUND OR NOT t.is_test THEN RAISE EXCEPTION 'owned immutable test task required'; END IF;
 IF t.test_purged_at IS NOT NULL THEN RETURN jsonb_build_object('task_id',target_task,'purged',true,'already_purged',true,'accounting_retained',true); END IF;
 -- Lock session/job rows before quiescence checks, serialize new admissions.
 PERFORM 1 FROM public.engine_sessions WHERE task_id=target_task FOR UPDATE;
 PERFORM 1 FROM public.worker_jobs WHERE task_id=target_task FOR UPDATE;
 PERFORM 1 FROM public.provider_attempts p JOIN public.worker_jobs j USING(job_id) WHERE j.task_id=target_task FOR UPDATE OF p;
 PERFORM 1 FROM public.retail_compute_segments r JOIN public.worker_jobs j USING(job_id) WHERE j.task_id=target_task FOR UPDATE OF r;
 IF t.source_dir IS NOT NULL OR t.state='failed' OR EXISTS(SELECT 1 FROM public.phases WHERE task_id=target_task AND state='failed')
  OR NOT EXISTS(SELECT 1 FROM public.decision_reviews WHERE task_id=target_task AND revision='child-operation-v1' AND payload::jsonb->'operation'->>'kind'='interview.start')
  OR NOT EXISTS(SELECT 1 FROM public.decision_reviews WHERE task_id=target_task AND revision='child-operation-v1' AND payload::jsonb->'operation'->>'kind'='interview.answer')
  OR EXISTS(SELECT 1 FROM public.worker_jobs WHERE task_id=target_task AND state<>'done')
  OR EXISTS(SELECT 1 FROM public.engine_sessions WHERE task_id=target_task AND (placement_instance IS NOT NULL OR lease_expires_at>extract(epoch FROM clock_timestamp())))
  OR EXISTS(SELECT 1 FROM public.provider_attempts p JOIN public.worker_jobs j USING(job_id) WHERE j.task_id=target_task AND (p.state NOT IN ('settled','cancelled') OR (p.state='settled' AND (p.expense_actual IS NULL OR p.retail_actual IS NULL))))
  OR EXISTS(SELECT 1 FROM public.retail_compute_segments r JOIN public.worker_jobs j USING(job_id) WHERE j.task_id=target_task AND r.state<>'settled')
  OR EXISTS(SELECT 1 FROM public.credit_charges c JOIN public.provider_attempts p USING(attempt_id) JOIN public.worker_jobs j ON j.job_id=p.job_id WHERE j.task_id=target_task AND c.state='pending')
  OR EXISTS(SELECT 1 FROM public.worker_jobs j WHERE j.task_id=target_task AND NOT EXISTS(SELECT 1 FROM public.decision_reviews d WHERE d.task_id=target_task AND d.wait_id='child-job:'||j.job_id AND d.revision='child-completion-v1' AND d.payload::jsonb->>'reaped'='true'))
 THEN RAISE EXCEPTION 'successful reaped settled child cycles required; content retained'; END IF;
 -- The acceptance journey has no reviewed revision lineage. Do not guess at
 -- deletion of immutable revision evidence or unknown external artifact paths.
 IF EXISTS(SELECT 1 FROM public.closed_revision_restart_intents WHERE task_id=target_task)
  OR EXISTS(SELECT 1 FROM public.closed_revision_lineage_plans WHERE task_id=target_task)
  OR EXISTS(SELECT 1 FROM public.task_artifacts WHERE task_id=target_task AND (sha256 IS NULL OR ref NOT LIKE 'pg-artifact:%%'))
 THEN RAISE EXCEPTION 'test content scope needs review; nothing purged'; END IF;
 SELECT array_agg(session_id),array_agg(COALESCE(published_thread_id,session_id)) INTO sessions,threads FROM public.engine_sessions WHERE task_id=target_task;
 threads=COALESCE(threads,ARRAY[]::text[])||COALESCE(sessions,ARRAY[]::text[]);
 IF EXISTS(SELECT 1 FROM public.engine_sessions WHERE task_id<>target_task AND (session_id=ANY(threads) OR published_thread_id=ANY(threads)))
 THEN RAISE EXCEPTION 'shared checkpoint thread; nothing purged'; END IF;
 SELECT array_agg(sha256) INTO hashes FROM public.task_artifacts WHERE task_id=target_task;
 DELETE FROM public.checkpoint_writes WHERE thread_id=ANY(threads);
 DELETE FROM public.checkpoint_blobs WHERE thread_id=ANY(threads);
 DELETE FROM public.checkpoints WHERE thread_id=ANY(threads);
 DELETE FROM public.engine_session_journal WHERE session_id=ANY(sessions);
 DELETE FROM public.staged_provider_responses WHERE owner_id=t.owner_id AND session_id=ANY(sessions);
 DELETE FROM public.generator_documents WHERE owner_id=t.owner_id AND execution_session_id=ANY(sessions);
 DELETE FROM public.generator_snapshots WHERE owner_id=t.owner_id AND task_id=target_task;
 DELETE FROM public.document_projections WHERE owner_id=t.owner_id AND execution_session_id=ANY(sessions);
 DELETE FROM public.final_packages WHERE owner_id=t.owner_id AND task_id=target_task;
 DELETE FROM public.artifact_manifest_ready WHERE owner_id=t.owner_id AND task_id=target_task;
 DELETE FROM public.task_artifacts WHERE task_id=target_task;
 DELETE FROM public.artifact_bytes b WHERE owner_id=t.owner_id AND sha256=ANY(hashes)
  AND NOT EXISTS(SELECT 1 FROM public.task_artifacts a JOIN public.tasks q USING(task_id) WHERE q.owner_id=t.owner_id AND a.sha256=b.sha256);
 DELETE FROM public.review_comments WHERE task_id=target_task;
 DELETE FROM public.decision_drafts WHERE task_id=target_task;
 DELETE FROM public.decision_reviews WHERE task_id=target_task;
 DELETE FROM public.answer_feedback WHERE task_id=target_task;
 DELETE FROM public.progress_ingest WHERE task_id=target_task;
 DELETE FROM public.progress_seen WHERE task_id=target_task;
 DELETE FROM public.events WHERE task_id=target_task;
 DELETE FROM public.gates WHERE task_id=target_task;
 DELETE FROM public.phases WHERE task_id=target_task;
 -- Operational logs are audit evidence; not silently deleted as product data.
 UPDATE public.engine_sessions SET control=NULL,published_thread_id=NULL,published_checkpoint_id=NULL WHERE task_id=target_task AND owner_id=t.owner_id;
 moment=extract(epoch FROM clock_timestamp());
 UPDATE public.tasks SET title='Purged test '||target_task,brief='',deleted_at=moment,test_purged_at=moment,updated_at=moment WHERE task_id=target_task;
 INSERT INTO public.audit_log(actor,action,task_id,owner_id,detail,ts) VALUES(auth.uid()::text,'test.content_purged',target_task,t.owner_id,'{"accounting_retained":true,"test":true}',moment);
 RETURN jsonb_build_object('task_id',target_task,'purged',true,'already_purged',false,'accounting_retained',true);
END;
$$;
REVOKE ALL ON FUNCTION public.requi_purge_test_task(TEXT,BOOLEAN) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.requi_purge_test_task(TEXT,BOOLEAN) TO requi_backend;
