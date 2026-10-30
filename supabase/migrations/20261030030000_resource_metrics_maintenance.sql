-- Fixed metrics-only maintenance entrypoint for abandoned host sources.
-- No caller-supplied SQL, identities, table names, or tenant claims. The
-- definer owns the fixed function; callers gain EXECUTE only, not cross-source
-- table access. Per-source locks match the writer and skip busy sources.
CREATE FUNCTION public.requi_maintain_resource_metrics(cutoff double precision, rolled_at double precision)
RETURNS TABLE(runs integer, days integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE source text; r record; d record;
BEGIN
 IF cutoff IS NULL OR rolled_at IS NULL OR cutoff<0 OR rolled_at<cutoff
    OR cutoff>=extract(epoch FROM clock_timestamp())-3600
    OR rolled_at>extract(epoch FROM clock_timestamp())+60
    OR NOT (cutoff<'Infinity'::double precision AND rolled_at<'Infinity'::double precision) THEN
  RAISE EXCEPTION 'invalid metrics retention timestamps' USING ERRCODE='22023';
 END IF;
 runs:=0;days:=0;
 FOR source IN
  SELECT source_id FROM (
   SELECT source_id FROM public.resource_run_samples WHERE ts<cutoff
   UNION SELECT source_id FROM public.resource_container_samples WHERE ts<cutoff
  ) s ORDER BY hashtextextended(source_id,floor(extract(epoch FROM clock_timestamp())/3600)::bigint),source_id LIMIT 32
 LOOP
  IF NOT pg_try_advisory_xact_lock(hashtextextended('resource-metrics:'||source,0)) THEN CONTINUE;END IF;
  FOR r IN SELECT run_key,min(task_id) task_id,min(session_id) session_id,min(phase) phase,min(pid) pid,
   min(ts) a,max(ts) b,count(*) n,avg(cpu_pct) ac,max(cpu_pct) mc,avg(rss_mb) ar,max(rss_mb) mr,max(cpu_seconds) cs,
   count(DISTINCT(task_id,session_id,phase,pid)) identities
   FROM public.resource_run_samples WHERE source_id=source GROUP BY run_key HAVING max(ts)<cutoff LIMIT 100
  LOOP
   IF r.identities<>1 THEN CONTINUE;END IF; -- preserve ambiguous evidence, never guess identity
   INSERT INTO public.resource_run_summaries VALUES(source,r.run_key,r.task_id,r.session_id,r.phase,r.pid,r.a,r.b,r.n,
    round(r.ac::numeric,1),round(r.mc::numeric,1),round(r.ar::numeric,1),round(r.mr::numeric,1),r.cs,rolled_at)
    ON CONFLICT(source_id,run_key) DO NOTHING;
   -- A conflicting summary must never silently discard unreconciled raw data.
   IF FOUND THEN
    DELETE FROM public.resource_run_samples WHERE source_id=source AND run_key=r.run_key;
    runs:=runs+1;
   END IF;
  END LOOP;
  FOR d IN SELECT to_char(to_timestamp(ts) AT TIME ZONE 'UTC','YYYY-MM-DD') bucket,
   count(*) n,avg(mem_used_mb) am,max(mem_used_mb) mm,avg(cpu_pct) ac,max(cpu_pct) mc
   FROM public.resource_container_samples WHERE source_id=source
   GROUP BY 1 HAVING max(ts)<cutoff LIMIT 100
  LOOP
   INSERT INTO public.resource_container_summaries VALUES(source,d.bucket,d.n,round(d.am::numeric,1),round(d.mm::numeric,1),
    round(d.ac::numeric,1),round(d.mc::numeric,1),rolled_at) ON CONFLICT(source_id,day) DO NOTHING;
   IF FOUND THEN
    DELETE FROM public.resource_container_samples WHERE source_id=source
     AND to_char(to_timestamp(ts) AT TIME ZONE 'UTC','YYYY-MM-DD')=d.bucket;
    days:=days+1;
   END IF;
  END LOOP;
 END LOOP;
 RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION public.requi_maintain_resource_metrics(double precision,double precision) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.requi_maintain_resource_metrics(double precision,double precision) TO requi_metrics_writer;
-- Supabase default ACLs can grant EXECUTE independently of PUBLIC.
DO $$ DECLARE r text; BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role','requi_backend','requi_operator_read'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON FUNCTION public.requi_maintain_resource_metrics(double precision,double precision) FROM %I',r);
  END IF;
 END LOOP;
END $$;
