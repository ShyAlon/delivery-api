-- Exact customer display projections; contribution/debit evidence remains authority.
CREATE TABLE public.retail_totals (
 owner_id UUID NOT NULL REFERENCES auth.users(id), scope TEXT NOT NULL CHECK(scope IN ('task','task_phase','job_phase')),
 ref TEXT NOT NULL,phase_key TEXT NOT NULL,n BIGINT NOT NULL CHECK(n>=0),
 credits NUMERIC NOT NULL CHECK(credits>=0),usd NUMERIC NOT NULL CHECK(usd>=0),posted_units NUMERIC NOT NULL CHECK(posted_units>=0),
 PRIMARY KEY(owner_id,scope,ref,phase_key)
);
ALTER TABLE public.retail_totals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retail_totals FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_retail_totals ON public.retail_totals FOR SELECT TO requi_backend USING(public.requi_session_active() AND owner_id=auth.uid());
CREATE FUNCTION public.requi_retail_total_delta(r public.retail_contributions,sign INT,task_override TEXT DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE task TEXT; item RECORD; amount NUMERIC=r.exact_units/1000000000; dollars NUMERIC;
BEGIN
 SELECT task_id INTO task FROM public.worker_jobs WHERE job_id=r.job_id;
 task=COALESCE(task_override,task);
 dollars=amount*(r.rates->>'usd_per_credit')::numeric;
 IF dollars IS NULL OR dollars<0 OR dollars IN ('Infinity'::numeric,'-Infinity'::numeric,'NaN'::numeric) THEN RAISE EXCEPTION 'valid frozen USD rate required'; END IF;
 FOR item IN SELECT * FROM (VALUES('task',task,''),('task_phase',task,r.phase_key),('job_phase',r.job_id,r.phase_key)) AS v(scope,ref,phase) LOOP
  UPDATE public.retail_totals SET n=n+sign,credits=credits+sign*amount,usd=usd+sign*dollars,posted_units=posted_units+sign*r.posted_units
  WHERE owner_id=r.owner_id AND scope=item.scope AND ref=item.ref AND phase_key=item.phase;
  IF NOT FOUND THEN INSERT INTO public.retail_totals VALUES(r.owner_id,item.scope,item.ref,item.phase,sign,sign*amount,sign*dollars,sign*r.posted_units); END IF;
 END LOOP;
END $$;
CREATE FUNCTION public.requi_retail_total_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN PERFORM public.requi_retail_total_delta(OLD,-1); END IF;
 IF TG_OP<>'DELETE' THEN PERFORM public.requi_retail_total_delta(NEW,1); END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
-- One-time owner-reviewed backfill, not a read-time history rescan.
DO $$ DECLARE r public.retail_contributions; BEGIN
 FOR r IN SELECT * FROM public.retail_contributions ORDER BY contribution_key LOOP PERFORM public.requi_retail_total_delta(r,1); END LOOP;
END $$;
CREATE TRIGGER retail_total_changed AFTER INSERT OR UPDATE OR DELETE ON public.retail_contributions FOR EACH ROW EXECUTE FUNCTION public.requi_retail_total_changed();
-- Explicit job reassignment repair, not part of the ordinary append/read path.
CREATE FUNCTION public.requi_retail_job_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r public.retail_contributions;
BEGIN
 IF NEW.task_id<>OLD.task_id THEN
  FOR r IN SELECT * FROM public.retail_contributions WHERE job_id=NEW.job_id ORDER BY contribution_key LOOP
   PERFORM public.requi_retail_total_delta(r,-1,OLD.task_id);
   PERFORM public.requi_retail_total_delta(r,1,NEW.task_id);
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER retail_job_attribution BEFORE UPDATE OF task_id ON public.worker_jobs FOR EACH ROW EXECUTE FUNCTION public.requi_retail_job_changed();
DO $$ DECLARE role TEXT; BEGIN
 FOREACH role IN ARRAY ARRAY['PUBLIC','anon','authenticated','service_role','requi_backend'] LOOP
  IF role='PUBLIC' OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=role) THEN
   EXECUTE format('REVOKE ALL ON public.retail_totals FROM %s',CASE WHEN role='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(role) END);
   EXECUTE format('REVOKE ALL ON FUNCTION public.requi_retail_total_delta(public.retail_contributions,int,text),public.requi_retail_total_changed(),public.requi_retail_job_changed() FROM %s',CASE WHEN role='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(role) END);
  END IF;
 END LOOP;
END $$;
GRANT SELECT ON public.retail_totals TO requi_backend;
