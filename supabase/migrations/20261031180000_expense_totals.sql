-- Private exact projections; no policy/activation change. Owner-run schema rollout.
CREATE TABLE public.expense_totals(bucket TEXT PRIMARY KEY,value NUMERIC NOT NULL CHECK(value>=0));
CREATE TABLE public.expense_total_clock(singleton BOOLEAN PRIMARY KEY CHECK(singleton),as_of DOUBLE PRECISION NOT NULL);
CREATE TABLE public.expense_total_entries(attempt_id TEXT PRIMARY KEY REFERENCES public.provider_attempts(attempt_id) ON DELETE CASCADE,parts JSONB NOT NULL,next_at DOUBLE PRECISION);
CREATE INDEX expense_due ON public.expense_total_entries(next_at) WHERE next_at IS NOT NULL;
ALTER TABLE public.expense_totals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_totals FORCE ROW LEVEL SECURITY;
ALTER TABLE public.expense_total_clock ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_total_clock FORCE ROW LEVEL SECURITY;
ALTER TABLE public.expense_total_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_total_entries FORCE ROW LEVEL SECURITY;
CREATE FUNCTION public.requi_expense_delta(parts JSONB,sign INT) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE k TEXT; v JSONB;
BEGIN
 FOR k,v IN SELECT * FROM jsonb_each(parts) LOOP
  UPDATE public.expense_totals SET value=value+sign*(v::numeric) WHERE bucket=k;
  IF NOT FOUND THEN INSERT INTO public.expense_totals VALUES(k,sign*(v::numeric)); END IF;
 END LOOP;
END $$;
CREATE FUNCTION public.requi_expense_project(a public.provider_attempts,moment DOUBLE PRECISION) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE parts JSONB='{}'; next_time DOUBLE PRECISION; owner TEXT=a.owner_id::text; sid TEXT; active BOOLEAN;
BEGIN
 IF a.state='uncertain' THEN parts=jsonb_build_object('unknown',1,'unknown:'||owner,1); END IF;
 IF a.state='settled' THEN
  SELECT session_id INTO sid FROM public.worker_jobs WHERE job_id=a.job_id;
  parts=parts||jsonb_build_object('session:'||owner||':'||sid,COALESCE(a.expense_actual,0));
  active=a.settled_at>moment-86400 AND a.settled_at<=moment;
  IF active THEN parts=parts||jsonb_build_object('platform',COALESCE(a.expense_actual,0)); END IF;
  IF a.settled_at>moment THEN next_time=a.settled_at;
  ELSIF active THEN next_time=a.settled_at+86400; END IF;
  IF a.quote->>'provider'='deepseek' THEN
   IF a.settled_at IS NULL OR a.settled_at>moment THEN parts=parts||jsonb_build_object('bad:'||owner,1); END IF;
   IF active THEN
    parts=parts||jsonb_build_object('own:'||owner,COALESCE(a.expense_actual,0),'active:'||owner,1);
    IF a.expense_actual IS NOT NULL AND a.quote->>'expense_units_per_usd' IS NOT NULL THEN
     parts=parts||jsonb_build_object('valid:'||owner||':'||(a.quote->>'expense_units_per_usd'),1);
    END IF;
   END IF;
  END IF;
 END IF;
 INSERT INTO public.expense_total_entries VALUES(a.attempt_id,parts,next_time);
 PERFORM public.requi_expense_delta(parts,1);
END $$;
-- All attempt writes acquire global before the existing owner projection lock.
CREATE FUNCTION public.requi_expense_write_lock() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER aa_expense_write_lock BEFORE INSERT OR UPDATE OR DELETE ON public.provider_attempts FOR EACH ROW EXECUTE FUNCTION public.requi_expense_write_lock();
CREATE FUNCTION public.requi_expense_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE prior JSONB; moment DOUBLE PRECISION;
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 SELECT as_of INTO moment FROM public.expense_total_clock WHERE singleton;
 IF NOT FOUND THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
 IF TG_OP<>'INSERT' THEN
  DELETE FROM public.expense_total_entries WHERE attempt_id=OLD.attempt_id RETURNING parts INTO prior;
  IF FOUND THEN PERFORM public.requi_expense_delta(prior,-1); END IF;
 END IF;
 IF TG_OP<>'DELETE' THEN PERFORM public.requi_expense_project(NEW,moment); END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER expense_attempt_delete BEFORE DELETE ON public.provider_attempts FOR EACH ROW EXECUTE FUNCTION public.requi_expense_changed();
CREATE TRIGGER expense_attempt_write AFTER INSERT OR UPDATE OF owner_id,job_id,state,expense_actual,settled_at,quote ON public.provider_attempts FOR EACH ROW EXECUTE FUNCTION public.requi_expense_changed();
-- Job attribution repairs are uncommon, explicit O(job history), not hot-path work.
CREATE FUNCTION public.requi_expense_job_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE a public.provider_attempts; old_parts JSONB; moment DOUBLE PRECISION;
BEGIN
 IF NEW.owner_id=OLD.owner_id AND NEW.session_id=OLD.session_id THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 SELECT as_of INTO moment FROM public.expense_total_clock WHERE singleton;
 IF NOT FOUND THEN RETURN NEW; END IF;
 FOR a IN SELECT * FROM public.provider_attempts WHERE job_id=NEW.job_id LOOP
  DELETE FROM public.expense_total_entries WHERE attempt_id=a.attempt_id RETURNING parts INTO old_parts;
  IF FOUND THEN PERFORM public.requi_expense_delta(old_parts,-1); END IF;
  PERFORM public.requi_expense_project(a,moment);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER expense_job_attribution AFTER UPDATE OF owner_id,session_id ON public.worker_jobs FOR EACH ROW EXECUTE FUNCTION public.requi_expense_job_changed();
CREATE FUNCTION public.requi_expense_users() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 IF EXISTS(SELECT 1 FROM public.expense_total_clock) THEN
  PERFORM public.requi_expense_delta(jsonb_build_object('users',1),CASE WHEN TG_OP='INSERT' THEN 1 ELSE -1 END);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER expense_user_write AFTER INSERT OR DELETE ON auth.users FOR EACH ROW EXECUTE FUNCTION public.requi_expense_users();
CREATE FUNCTION public.requi_refresh_expense_totals(moment DOUBLE PRECISION) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE old_time DOUBLE PRECISION; a public.provider_attempts; due RECORD;
BEGIN
 PERFORM pg_advisory_xact_lock(73194720315621::bigint);
 IF moment IS NULL OR moment IN ('Infinity'::float8,'-Infinity'::float8,'NaN'::float8) THEN RAISE EXCEPTION 'finite time required'; END IF;
 SELECT as_of INTO old_time FROM public.expense_total_clock WHERE singleton;
 IF NOT FOUND OR moment<old_time THEN
  DELETE FROM public.expense_total_entries; DELETE FROM public.expense_totals;
  INSERT INTO public.expense_totals SELECT 'users',count(*) FROM auth.users;
  INSERT INTO public.expense_total_clock VALUES(true,moment) ON CONFLICT(singleton) DO UPDATE SET as_of=moment;
  FOR a IN SELECT * FROM public.provider_attempts LOOP PERFORM public.requi_expense_project(a,moment); END LOOP;
 ELSE
  FOR due IN DELETE FROM public.expense_total_entries WHERE next_at<=moment RETURNING attempt_id,parts LOOP
   PERFORM public.requi_expense_delta(due.parts,-1);
   SELECT * INTO a FROM public.provider_attempts WHERE attempt_id=due.attempt_id;
   PERFORM public.requi_expense_project(a,moment);
  END LOOP;
  UPDATE public.expense_total_clock SET as_of=moment WHERE singleton;
 END IF;
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
 IF COALESCE((SELECT value FROM public.expense_totals WHERE bucket='unknown'),0)>0 THEN RETURN 'unknown_provider_outcome'; END IF;
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
 IF COALESCE((SELECT value FROM public.expense_totals WHERE bucket='unknown:'||j.owner_id::text),0)>0 THEN RETURN 'own_test_unknown_provider_outcome'; END IF;
 IF COALESCE((SELECT value FROM public.expense_totals WHERE bucket='bad:'||j.owner_id::text),0)>0
  OR COALESCE((SELECT value FROM public.expense_totals WHERE bucket='active:'||j.owner_id::text),0)
    <>COALESCE((SELECT value FROM public.expense_totals WHERE bucket='valid:'||j.owner_id::text||':'||p.units_per_usd::text),0)
 THEN RETURN 'own_test_expense_history_unverifiable'; END IF;
 SELECT COALESCE((SELECT value FROM public.expense_totals WHERE bucket='own:'||j.owner_id::text),0) INTO spent;
 IF spent>=p.limit_units THEN RETURN 'own_test_rolling_24h_expense_limit'; END IF;
 RETURN 'allowed';
END;
$$;
DO $$ DECLARE r TEXT; BEGIN
 FOREACH r IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend'] LOOP
  IF r='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON public.expense_totals,public.expense_total_clock,public.expense_total_entries FROM %s',CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
   EXECUTE format('REVOKE ALL ON FUNCTION public.requi_expense_delta(jsonb,int),public.requi_expense_project(public.provider_attempts,double precision),public.requi_expense_changed(),public.requi_expense_write_lock(),public.requi_expense_users(),public.requi_expense_job_changed(),public.requi_refresh_expense_totals(double precision) FROM %s',CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
  END IF;
 END LOOP;
END $$;
