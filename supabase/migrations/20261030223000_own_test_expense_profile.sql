-- Explicit operator-owned test profile. Empty on installation; no owner auto-selected.
-- Separate from launch admission_limits: never relax its no-quota validator.
CREATE TABLE public.own_test_expense_profiles (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 policy_version TEXT NOT NULL CHECK(length(policy_version)>0),
 limit_units BIGINT NOT NULL CHECK(limit_units>0),
 units_per_usd BIGINT NOT NULL CHECK(units_per_usd>0),
 CHECK(limit_units::numeric=2*units_per_usd::numeric)
);
ALTER TABLE public.own_test_expense_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.own_test_expense_profiles FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.own_test_expense_profiles FROM PUBLIC,authenticated,requi_backend;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.own_test_expense_profiles FROM anon;
 END IF;
END $$;
CREATE FUNCTION public.requi_own_test_expense_guard(target_job TEXT,expense_scale BIGINT) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE j public.worker_jobs; p public.own_test_expense_profiles; moment DOUBLE PRECISION; spent NUMERIC;
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 IF NOT public.requi_session_active() THEN RETURN 'session_inactive'; END IF;
 SELECT * INTO j FROM public.worker_jobs WHERE job_id=target_job AND owner_id=auth.uid() AND state='running'
   AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid;
 IF NOT FOUND THEN RETURN 'worker_unavailable'; END IF;
 SELECT * INTO p FROM public.own_test_expense_profiles WHERE owner_id=j.owner_id;
 IF NOT FOUND THEN RETURN 'allowed'; END IF;
 IF expense_scale IS NULL OR expense_scale<>p.units_per_usd THEN RETURN 'own_test_expense_scale_mismatch'; END IF;
 -- No unknown/mixed-scale expense can be silently treated as zero or summed as dollars.
 IF EXISTS(SELECT 1 FROM public.provider_attempts WHERE owner_id=j.owner_id AND state='uncertain') THEN
  RETURN 'own_test_unknown_provider_outcome'; END IF;
 moment=extract(epoch FROM clock_timestamp());
 IF EXISTS(SELECT 1 FROM public.provider_attempts WHERE owner_id=j.owner_id AND state='settled'
    AND quote->>'provider'='deepseek' AND (settled_at IS NULL OR settled_at>moment OR (settled_at>moment-86400 AND settled_at<=moment
    AND (expense_actual IS NULL OR quote->>'expense_units_per_usd' IS DISTINCT FROM p.units_per_usd::text)))) THEN
  RETURN 'own_test_expense_history_unverifiable'; END IF;
 SELECT COALESCE(sum(expense_actual),0) INTO spent FROM public.provider_attempts
 WHERE owner_id=j.owner_id AND state='settled' AND quote->>'provider'='deepseek'
   AND settled_at>moment-86400 AND settled_at<=moment;
 IF spent>=p.limit_units THEN RETURN 'own_test_rolling_24h_expense_limit'; END IF;
 RETURN 'allowed';
END;
$$;
REVOKE ALL ON FUNCTION public.requi_own_test_expense_guard(TEXT,BIGINT) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION public.requi_own_test_expense_guard(TEXT,BIGINT) TO requi_backend;
