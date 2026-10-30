-- Process diagnostic data only. No tenant/credit authority in the writer role.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_log_writer') THEN
  CREATE ROLE requi_log_writer NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO requi_log_writer;
CREATE TABLE public.operator_log_records (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_id TEXT NOT NULL CHECK(length(source_id) BETWEEN 1 AND 200),
 stream TEXT NOT NULL CHECK(stream IN ('activity','diagnostics','engine','stage:intent','stage:conops','stage:needs','stage:srs','stage:architecture','stage:specs','stage:tests','stage:validation')),
 ts DOUBLE PRECISION NOT NULL CHECK(ts>=0 AND ts<'Infinity'::double precision),
 text TEXT NOT NULL CHECK(octet_length(text)<=32768),
 byte_size INTEGER NOT NULL CHECK(byte_size=octet_length(text)),
 task_id TEXT CHECK(length(task_id)<=240),session_id TEXT CHECK(length(session_id)<=240)
);
CREATE INDEX operator_log_source_stream ON public.operator_log_records(source_id,stream,id DESC);
CREATE INDEX operator_log_task ON public.operator_log_records(task_id,id DESC);
ALTER TABLE public.operator_log_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_log_records FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_log_records FROM PUBLIC,anon,authenticated,service_role,requi_backend;
REVOKE ALL ON SEQUENCE public.operator_log_records_id_seq FROM PUBLIC,anon,authenticated,service_role,requi_backend;
GRANT SELECT,INSERT,DELETE ON public.operator_log_records TO requi_log_writer;
CREATE POLICY log_writer ON public.operator_log_records TO requi_log_writer
 USING(source_id=current_setting('requi.log_source',true))
 WITH CHECK(source_id=current_setting('requi.log_source',true));
GRANT SELECT ON public.operator_log_records TO requi_operator_read;
CREATE POLICY log_operator ON public.operator_log_records FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('logs.read'));
-- Latest safe startup projection only, one bounded row per explicitly stable source.
CREATE TABLE public.operator_startup_reports (
 source_id TEXT PRIMARY KEY CHECK(length(source_id) BETWEEN 1 AND 200),
 body JSONB NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=32768)
);
ALTER TABLE public.operator_startup_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_startup_reports FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.operator_startup_reports FROM PUBLIC,anon,authenticated,service_role,requi_backend;
GRANT SELECT,INSERT,UPDATE ON public.operator_startup_reports TO requi_log_writer;
CREATE POLICY startup_writer ON public.operator_startup_reports TO requi_log_writer
 USING(source_id=current_setting('requi.log_source',true))
 WITH CHECK(source_id=current_setting('requi.log_source',true));
GRANT SELECT ON public.operator_startup_reports TO requi_operator_read;
CREATE POLICY startup_operator ON public.operator_startup_reports FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('logs.read'));
