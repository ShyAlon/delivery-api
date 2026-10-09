-- Metadata only. Does not grant events, error text, artifact paths or log bodies.
CREATE POLICY operator_task_debug_metadata ON public.tasks FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('debug.read'));
CREATE POLICY operator_job_debug_metadata ON public.worker_jobs FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('debug.read'));
