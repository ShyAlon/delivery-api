-- Read capabilities do not imply prompt/quote/provider raw evidence access.
REVOKE SELECT ON public.users,public.tasks,public.engine_sessions,public.worker_jobs,public.credit_accounts,public.provider_attempts FROM requi_operator_read;
GRANT SELECT(user_id,label,created_at,updated_at) ON public.users TO requi_operator_read;
GRANT SELECT(task_id,owner_id,state,active_phase,generation,created_at,updated_at,deleted_at) ON public.tasks TO requi_operator_read;
GRANT SELECT(session_id,owner_id,task_id,generation,created_at) ON public.engine_sessions TO requi_operator_read;
GRANT SELECT(job_id,owner_id,task_id,session_id,generation,kind,state,created_at,updated_at) ON public.worker_jobs TO requi_operator_read;
GRANT SELECT(owner_id,balance_units,units_per_credit,updated_at) ON public.credit_accounts TO requi_operator_read;
GRANT SELECT(attempt_id,owner_id,job_id,state,expense_reserved,expense_actual,retail_reserved,retail_actual,created_at,updated_at,settled_at) ON public.provider_attempts TO requi_operator_read;
-- Debug/log exports require their own redacted, reviewed capability surface.
