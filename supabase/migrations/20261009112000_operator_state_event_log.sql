-- New, schema-only state events. Never copy existing raw messages/payloads.
CREATE TABLE public.operator_state_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id UUID NOT NULL REFERENCES auth.users(id), task_id TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('task','worker')),
 event TEXT NOT NULL CHECK(event IN ('created','state_changed')),
 state TEXT NOT NULL CHECK(state IN ('draft','pending','running','paused','done','failed','waiting_gate','stop_requested','stopped','other')),
 generation INTEGER NOT NULL, ts DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.operator_state_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_state_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_state_events FROM PUBLIC,authenticated,requi_backend,requi_operator_read;
GRANT SELECT ON public.operator_state_events TO requi_operator_read;
CREATE POLICY operator_state_log_read ON public.operator_state_events FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('logs.read'));
CREATE FUNCTION public.requi_capture_operator_state() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.state IS NOT DISTINCT FROM OLD.state THEN RETURN NEW; END IF;
 INSERT INTO public.operator_state_events(owner_id,task_id,source,event,state,generation,ts)
 VALUES (NEW.owner_id,NEW.task_id,CASE WHEN TG_TABLE_NAME='tasks' THEN 'task' ELSE 'worker' END,
 CASE WHEN TG_OP='INSERT' THEN 'created' ELSE 'state_changed' END,
 CASE WHEN NEW.state IN ('draft','pending','running','paused','done','failed','waiting_gate','stop_requested','stopped') THEN NEW.state ELSE 'other' END,
 NEW.generation,extract(epoch FROM statement_timestamp()));
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.requi_capture_operator_state() FROM PUBLIC,authenticated,requi_backend,requi_operator_read;
CREATE TRIGGER operator_task_state_event AFTER INSERT OR UPDATE OF state ON public.tasks
 FOR EACH ROW EXECUTE FUNCTION public.requi_capture_operator_state();
CREATE TRIGGER operator_worker_state_event AFTER INSERT OR UPDATE OF state ON public.worker_jobs
 FOR EACH ROW EXECUTE FUNCTION public.requi_capture_operator_state();
-- No historical backfill. No raw-log download or tenant-browser read grant.
