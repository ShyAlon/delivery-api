-- Exact incremental projection; retained attempt history remains authoritative.
-- Lazy one-time rebuild per owner/policy change. No runtime DB application.
CREATE TABLE public.admission_totals (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 period_seconds BIGINT NOT NULL CHECK(period_seconds>0), rate_window_seconds BIGINT NOT NULL CHECK(rate_window_seconds>0),
 as_of DOUBLE PRECISION NOT NULL,
 overruns BIGINT NOT NULL DEFAULT 0 CHECK(overruns>=0), held BIGINT NOT NULL DEFAULT 0 CHECK(held>=0),
 uncertain BIGINT NOT NULL DEFAULT 0 CHECK(uncertain>=0), recent BIGINT NOT NULL DEFAULT 0 CHECK(recent>=0),
 used_expense NUMERIC NOT NULL DEFAULT 0 CHECK(used_expense>=0), used_retail NUMERIC NOT NULL DEFAULT 0 CHECK(used_retail>=0)
);
CREATE TABLE public.admission_total_entries (
 attempt_id TEXT PRIMARY KEY REFERENCES public.provider_attempts(attempt_id) ON DELETE CASCADE,
 owner_id UUID NOT NULL REFERENCES public.admission_totals(owner_id) ON DELETE CASCADE,
 overruns INT NOT NULL, held INT NOT NULL, uncertain INT NOT NULL, recent INT NOT NULL,
 expense NUMERIC NOT NULL, retail NUMERIC NOT NULL,
 rate_expires DOUBLE PRECISION, spend_expires DOUBLE PRECISION
);
CREATE INDEX admission_entry_owner ON public.admission_total_entries(owner_id);
CREATE INDEX admission_rate_expiry ON public.admission_total_entries(owner_id,rate_expires) WHERE rate_expires IS NOT NULL;
CREATE INDEX admission_spend_expiry ON public.admission_total_entries(owner_id,spend_expires) WHERE spend_expires IS NOT NULL;
ALTER TABLE public.admission_totals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admission_totals FORCE ROW LEVEL SECURITY;
ALTER TABLE public.admission_total_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admission_total_entries FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_admission_totals ON public.admission_totals FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());

-- Called only by the trusted trigger/read function with an actual ledger row.
CREATE FUNCTION public.requi_admission_project(a public.provider_attempts) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE t public.admission_totals; h INT; r INT; x NUMERIC; y NUMERIC; expiry DOUBLE PRECISION;
BEGIN
 SELECT * INTO t FROM public.admission_totals WHERE owner_id=a.owner_id FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 h=(a.state IN ('reserved','dispatched','uncertain'))::int;
 r=(a.created_at>t.as_of-t.rate_window_seconds)::int;
 expiry=CASE WHEN a.state='settled' AND a.created_at>t.as_of-t.period_seconds THEN a.created_at+t.period_seconds END;
 x=CASE WHEN h=1 OR a.state='failure_waived' THEN a.expense_reserved WHEN expiry IS NOT NULL THEN a.expense_actual ELSE 0 END;
 y=CASE WHEN h=1 OR a.state='failure_waived' THEN a.retail_reserved WHEN expiry IS NOT NULL THEN a.retail_actual ELSE 0 END;
 IF x IS NULL OR y IS NULL THEN RAISE EXCEPTION 'settled admission evidence missing'; END IF;
 INSERT INTO public.admission_total_entries VALUES(a.attempt_id,a.owner_id,a.input_estimate_overrun::int,h,(a.state='uncertain')::int,r,x,y,CASE WHEN r=1 THEN a.created_at+t.rate_window_seconds END,expiry);
 UPDATE public.admission_totals SET overruns=overruns+a.input_estimate_overrun::int,held=held+h,
 uncertain=uncertain+(a.state='uncertain')::int,recent=recent+r,used_expense=used_expense+x,used_retail=used_retail+y WHERE owner_id=a.owner_id;
END $$;
CREATE FUNCTION public.requi_admission_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE e public.admission_total_entries; owner UUID;
BEGIN
 IF TG_OP='UPDATE' AND NEW.owner_id<>OLD.owner_id THEN RAISE EXCEPTION 'attempt owner is immutable'; END IF;
 owner=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to(owner::text,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 -- The row lock also serializes projection changes with admission/expiry.
 PERFORM 1 FROM public.admission_totals WHERE owner_id=owner FOR UPDATE;
 IF TG_OP<>'INSERT' THEN
  DELETE FROM public.admission_total_entries WHERE attempt_id=OLD.attempt_id RETURNING * INTO e;
  IF FOUND THEN
   UPDATE public.admission_totals SET overruns=overruns-e.overruns,held=held-e.held,
    uncertain=uncertain-e.uncertain,recent=recent-e.recent,
    used_expense=used_expense-e.expense,used_retail=used_retail-e.retail WHERE owner_id=owner;
  END IF;
 END IF;
 IF TG_OP<>'DELETE' THEN PERFORM public.requi_admission_project(NEW); RETURN NEW; END IF;
 RETURN OLD;
END $$;
-- BEFORE DELETE so the entry is still present before FK cascades.
CREATE TRIGGER admission_attempt_changed BEFORE DELETE ON public.provider_attempts FOR EACH ROW EXECUTE FUNCTION public.requi_admission_changed();
CREATE TRIGGER admission_attempt_written AFTER INSERT OR UPDATE OF owner_id,state,expense_reserved,retail_reserved,expense_actual,retail_actual,created_at,input_estimate_overrun ON public.provider_attempts FOR EACH ROW EXECUTE FUNCTION public.requi_admission_changed();

CREATE FUNCTION public.requi_admission_totals(moment DOUBLE PRECISION)
RETURNS TABLE(overruns BIGINT,held BIGINT,uncertain BIGINT,recent BIGINT,used_expense NUMERIC,used_retail NUMERIC)
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE owner UUID; p public.admission_limits; t public.admission_totals; a public.provider_attempts; n BIGINT; x NUMERIC; y NUMERIC;
BEGIN
 IF NOT public.requi_session_active() THEN RAISE EXCEPTION 'active session required'; END IF;
 IF moment IS NULL OR moment IN ('Infinity'::float8,'-Infinity'::float8,'NaN'::float8) THEN RAISE EXCEPTION 'finite time required'; END IF;
 owner=auth.uid();
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to(owner::text,'UTF8')),'hex'),1,16))::bit(64)::bigint);
 SELECT * INTO p FROM public.admission_limits WHERE owner_id=owner;
 IF NOT FOUND THEN RAISE EXCEPTION 'reviewed owner admission configuration missing'; END IF;
 -- The Python admission path already holds its owner advisory lock. FOR UPDATE
 -- below is the projection's common serialization point for every writer.
 SELECT * INTO t FROM public.admission_totals WHERE owner_id=owner FOR UPDATE;
 IF FOUND AND (t.period_seconds<>p.period_seconds OR t.rate_window_seconds<>p.rate_window_seconds OR moment<t.as_of) THEN
  DELETE FROM public.admission_totals WHERE owner_id=owner; t.owner_id=NULL;
 END IF;
 IF t.owner_id IS NULL THEN
  -- Concurrent first reads serialize on this insert. Re-read/lock after conflict.
  INSERT INTO public.admission_totals(owner_id,period_seconds,rate_window_seconds,as_of) VALUES(owner,p.period_seconds,p.rate_window_seconds,moment) ON CONFLICT DO NOTHING;
  SELECT * INTO t FROM public.admission_totals WHERE owner_id=owner FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM public.admission_total_entries WHERE owner_id=owner) THEN
   FOR a IN SELECT * FROM public.provider_attempts WHERE owner_id=owner LOOP
    PERFORM public.requi_admission_project(a);
   END LOOP;
  END IF;
 END IF;
 -- Touch each expired contribution once, not the retained lifetime history.
 WITH expired AS (
  UPDATE public.admission_total_entries SET recent=0,rate_expires=NULL
  WHERE owner_id=owner AND rate_expires<=moment RETURNING 1
 ) SELECT count(*) INTO n FROM expired;
 -- Preserve the old amounts while clearing them for exact subtraction.
 WITH due AS MATERIALIZED (
  SELECT attempt_id,expense,retail FROM public.admission_total_entries WHERE owner_id=owner AND spend_expires<=moment FOR UPDATE
 ), cleared AS (
  UPDATE public.admission_total_entries e SET expense=0,retail=0,spend_expires=NULL FROM due d WHERE e.attempt_id=d.attempt_id RETURNING d.expense,d.retail
 ) SELECT COALESCE(sum(expense),0),COALESCE(sum(retail),0) INTO x,y FROM cleared;
 UPDATE public.admission_totals z SET recent=z.recent-n,used_expense=z.used_expense-x,used_retail=z.used_retail-y,as_of=moment WHERE owner_id=owner;
 RETURN QUERY SELECT z.overruns,z.held,z.uncertain,z.recent,z.used_expense,z.used_retail FROM public.admission_totals z WHERE owner_id=owner;
END $$;
-- Projection is private and cannot be forged through Supabase default ACLs.
DO $$ DECLARE r TEXT; BEGIN
 FOREACH r IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend'] LOOP
  IF r='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON public.admission_totals,public.admission_total_entries FROM %s',CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
   EXECUTE format('REVOKE ALL ON FUNCTION public.requi_admission_project(public.provider_attempts),public.requi_admission_changed(),public.requi_admission_totals(double precision) FROM %s',CASE WHEN r='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(r) END);
  END IF;
 END LOOP;
END $$;
GRANT SELECT ON public.admission_totals TO requi_backend;
GRANT EXECUTE ON FUNCTION public.requi_admission_totals(double precision) TO requi_backend;
