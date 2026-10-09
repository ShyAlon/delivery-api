-- Private confirmed-expense admission, disabled until operator config exists.
-- No browser grants, provider contact, SMTP delivery or execution enablement.
CREATE TABLE public.expense_guard_policy (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
 policy_version TEXT NOT NULL CHECK(length(policy_version)>0),
 session_units BIGINT NOT NULL CHECK(session_units>0),
 per_registered_user_units BIGINT NOT NULL CHECK(per_registered_user_units>0),
 units_per_usd BIGINT NOT NULL CHECK(units_per_usd>0),
 max_alert_attempts BIGINT NOT NULL CHECK(max_alert_attempts>0)
);
CREATE TABLE public.expense_guard_episodes (
 scope_key TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
 blocked BOOLEAN NOT NULL, changed_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.expense_guard_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_guard_policy FORCE ROW LEVEL SECURITY;
ALTER TABLE public.expense_guard_episodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_guard_episodes FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.expense_guard_policy,public.expense_guard_episodes FROM PUBLIC,authenticated,requi_backend;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.expense_guard_policy,public.expense_guard_episodes FROM anon;
 END IF;
END $$;
-- The definer returns only an admission reason, never cross-owner ledger rows.
CREATE FUNCTION public.requi_confirmed_expense_guard(target_job TEXT, expense_scale BIGINT DEFAULT NULL) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE
 j public.worker_jobs; p public.expense_guard_policy; moment DOUBLE PRECISION;
 session_spent NUMERIC; platform_spent NUMERIC; user_count BIGINT; cap NUMERIC;
 breached BOOLEAN; scope_name TEXT; scope_id TEXT; guard_key TEXT;
 used NUMERIC; lim NUMERIC; ep public.expense_guard_episodes; episode TEXT;
 reason TEXT; payload JSONB; outbox_key TEXT;
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 IF NOT public.requi_session_active() THEN RETURN 'session_inactive'; END IF;
 SELECT * INTO j FROM public.worker_jobs WHERE job_id=target_job AND owner_id=auth.uid() AND state='running'
   AND auth_session_id=nullif(current_setting('request.jwt.claim.session_id',true),'')::uuid;
 IF NOT FOUND THEN RETURN 'worker_unavailable'; END IF;
 SELECT * INTO p FROM public.expense_guard_policy WHERE singleton;
 IF NOT FOUND THEN RETURN 'expense_policy_missing'; END IF;
 IF expense_scale IS NOT NULL AND expense_scale<>p.units_per_usd THEN RETURN 'expense_scale_mismatch'; END IF;
 moment=extract(epoch FROM clock_timestamp());
 -- Confirmed costs only, not estimates or reserved worst-case quotes.
 SELECT COALESCE(sum(a.expense_actual),0) INTO session_spent
 FROM public.provider_attempts a JOIN public.worker_jobs w ON w.job_id=a.job_id
 WHERE a.state='settled' AND w.owner_id=j.owner_id AND w.session_id=j.session_id;
 SELECT COALESCE(sum(expense_actual),0) INTO platform_spent FROM public.provider_attempts
 WHERE state='settled' AND settled_at>moment-86400 AND settled_at<=moment;
 SELECT count(*) INTO user_count FROM auth.users;
 cap=p.per_registered_user_units::numeric*user_count;
 FOR scope_name,scope_id,used,lim IN
   SELECT 'session',j.owner_id::text||':'||j.session_id,session_spent,p.session_units::numeric
   UNION ALL SELECT 'platform_rolling_24h','platform',platform_spent,cap
 LOOP
   guard_key=scope_name||':'||scope_id;
   breached=used>=lim;
   SELECT * INTO ep FROM public.expense_guard_episodes e WHERE e.scope_key=guard_key;
   IF breached THEN
     IF NOT FOUND OR NOT ep.blocked THEN
       episode=md5(guard_key||':'||moment::text||':'||random()::text);
       INSERT INTO public.expense_guard_episodes VALUES(guard_key,episode,true,moment)
       ON CONFLICT(scope_key) DO UPDATE SET episode_id=excluded.episode_id,blocked=true,changed_at=excluded.changed_at;
       payload=jsonb_build_object('episode_id',episode,'scope',scope_name,'scope_id',scope_id,
         'spent_units',used,'limit_units',lim,'units_per_usd',p.units_per_usd,'occurred_at',moment,
         'action','new_calls_stopped','running_calls','finish','accounting','confirmed_provider_charges',
         'policy_version',p.policy_version);
       outbox_key=md5(episode||guard_key);
       INSERT INTO public.expense_alert_outbox VALUES(outbox_key,j.owner_id,payload,'pending',0,p.max_alert_attempts,moment,moment)
       ON CONFLICT(alert_key) DO NOTHING;
     END IF;
     reason=COALESCE(reason,scope_name||'_expense_limit');
   ELSIF FOUND AND ep.blocked THEN
     UPDATE public.expense_guard_episodes e SET blocked=false,changed_at=moment WHERE e.scope_key=guard_key;
   END IF;
 END LOOP;
 -- Unknown outcomes are not free. Running/dispatched calls may finish normally.
 IF EXISTS(SELECT 1 FROM public.provider_attempts WHERE state='uncertain') THEN RETURN 'unknown_provider_outcome'; END IF;
 RETURN COALESCE(reason,'allowed');
END;
$$;
REVOKE ALL ON FUNCTION public.requi_confirmed_expense_guard(TEXT,BIGINT) FROM PUBLIC,authenticated;
GRANT EXECUTE ON FUNCTION public.requi_confirmed_expense_guard(TEXT,BIGINT) TO requi_backend;
