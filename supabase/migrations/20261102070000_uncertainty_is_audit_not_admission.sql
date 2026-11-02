-- Owner direction: uncertainty warns, never stops unrelated admission.
-- Keep actual/reserved checks, all attempts, raw evidence, and no-auto-replay guards.
-- Residual risk: final cost can exceed a ceiling by the unknown bill.
CREATE OR REPLACE FUNCTION public.requi_admission_project(a public.provider_attempts) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE t public.admission_totals; h INT; r INT; x NUMERIC; y NUMERIC; expiry DOUBLE PRECISION;
BEGIN
 SELECT * INTO t FROM public.admission_totals WHERE owner_id=a.owner_id FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 h=(a.state IN ('reserved','dispatched'))::int;
 r=(a.created_at>t.as_of-t.rate_window_seconds)::int;
 expiry=CASE WHEN a.state='settled' AND a.created_at>t.as_of-t.period_seconds THEN a.created_at+t.period_seconds END;
 x=CASE WHEN h=1 OR a.state IN ('uncertain','failure_waived') THEN a.expense_reserved WHEN expiry IS NOT NULL THEN a.expense_actual ELSE 0 END;
 y=CASE WHEN h=1 OR a.state IN ('uncertain','failure_waived') THEN a.retail_reserved WHEN expiry IS NOT NULL THEN a.retail_actual ELSE 0 END;
 IF x IS NULL OR y IS NULL THEN RAISE EXCEPTION 'settled admission evidence missing'; END IF;
 INSERT INTO public.admission_total_entries VALUES(a.attempt_id,a.owner_id,(a.input_estimate_overrun AND a.state<>'uncertain')::int,h,(a.state='uncertain')::int,r,x,y,CASE WHEN r=1 THEN a.created_at+t.rate_window_seconds END,expiry);
 UPDATE public.admission_totals SET overruns=overruns+(a.input_estimate_overrun AND a.state<>'uncertain')::int,held=held+h,
 uncertain=uncertain+(a.state='uncertain')::int,recent=recent+r,used_expense=used_expense+x,used_retail=used_retail+y WHERE owner_id=a.owner_id;
END $$;
CREATE OR REPLACE FUNCTION public.requi_confirmed_expense_guard(target_job TEXT, expense_scale BIGINT DEFAULT NULL) RETURNS TEXT
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
 PERFORM public.requi_refresh_expense_totals(moment);
 SELECT COALESCE((SELECT value FROM public.expense_totals WHERE bucket='session:'||j.owner_id::text||':'||j.session_id),0) INTO session_spent;
 SELECT COALESCE((SELECT value FROM public.expense_totals WHERE bucket='platform'),0) INTO platform_spent;
 SELECT COALESCE((SELECT value FROM public.expense_totals WHERE bucket='users'),0) INTO user_count;
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
 -- Unknown bills are operator warnings; confirmed amount limits still apply.
 RETURN COALESCE(reason,'allowed');
END;
$$;
CREATE OR REPLACE FUNCTION public.requi_own_test_expense_guard(target_job TEXT,expense_scale BIGINT) RETURNS TEXT
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
 moment=extract(epoch FROM clock_timestamp());
 PERFORM public.requi_refresh_expense_totals(moment);
 -- Unknown outcomes retain their evidence but do not block this owner.
 IF COALESCE((SELECT value FROM public.expense_totals WHERE bucket='bad:'||j.owner_id::text),0)>0
  OR COALESCE((SELECT value FROM public.expense_totals WHERE bucket='active:'||j.owner_id::text),0)
    <>COALESCE((SELECT value FROM public.expense_totals WHERE bucket='valid:'||j.owner_id::text||':'||p.units_per_usd::text),0)
 THEN RETURN 'own_test_expense_history_unverifiable'; END IF;
 SELECT COALESCE((SELECT value FROM public.expense_totals WHERE bucket='own:'||j.owner_id::text),0) INTO spent;
 IF spent>=p.limit_units THEN RETURN 'own_test_rolling_24h_expense_limit'; END IF;
 RETURN 'allowed';
END;
$$;

-- Lazy projections rebuild on their next read. No authoritative ledger row changes.
DELETE FROM public.admission_totals;

-- Explicit catalog marker: function identity changes above otherwise already exist.
CREATE FUNCTION public.requi_uncertainty_warning_policy() RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path='' AS $$ SELECT true $$;
REVOKE ALL ON FUNCTION public.requi_uncertainty_warning_policy() FROM PUBLIC;
