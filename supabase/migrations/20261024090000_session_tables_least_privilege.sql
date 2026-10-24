-- Least-privilege completion for the session tables (PR A/B schema).
-- Supabase projects grant ALL privileges on new public-schema tables to anon,
-- authenticated and service_role through default ACLs (pg_default_acl), and
-- PostgreSQL itself grants EXECUTE on new functions to PUBLIC. The session-table
-- migrations revoke PUBLIC and authenticated; this additive hardening completes
-- the same intent for the remaining default-granted roles. It does not weaken any
-- existing grant or policy: requi_backend keeps exactly its reviewed privileges.
-- Idempotent; roles that do not exist are skipped (plain Postgres has no anon,
-- authenticated or service_role). Filename ordering places this AFTER the
-- session-table migrations; if they are missing the chain is broken, so it fails
-- loudly rather than silently skipping the hardening.
DO $$
DECLARE r TEXT;
BEGIN
  IF to_regclass('public.staged_provider_responses') IS NULL THEN
    RAISE EXCEPTION 'session tables are missing - this hardening must apply after the session-table migrations (filename order); reconcile the chain';
  ELSE
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
        EXECUTE format('REVOKE ALL ON public.engine_session_journal, public.checkpoint_migrations, public.checkpoints, public.checkpoint_blobs, public.checkpoint_writes, public.artifact_bytes, public.artifact_manifest_ready, public.producer_results, public.staged_provider_responses FROM %I', r);
        EXECUTE format('REVOKE ALL ON FUNCTION public.staged_provider_responses_guard() FROM %I', r);
      END IF;
    END LOOP;
    REVOKE ALL ON FUNCTION public.staged_provider_responses_guard() FROM PUBLIC;
  END IF;
END $$;
