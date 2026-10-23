-- Operator reservation-release audit. Lets the read-only operator database
-- role also see reservation-release audit rows (releases after refusal,
-- releases with proof, and failed releases needing attention), and nothing
-- else new. The stop-audit migration remains the registry and role setup;
-- this file only adds one permissive policy. Repeatable. The back office
-- endpoint keeps returning its not-configured guidance until this file is
-- applied in the Supabase SQL editor.
DROP POLICY IF EXISTS operator_attempt_release_audit_read ON public.audit_log;
CREATE POLICY operator_attempt_release_audit_read ON public.audit_log FOR SELECT TO requi_operator_read
  USING (action IN ('provider.attempt.cancelled','provider.attempt.released','provider.attempt.release_failed')
         AND public.requi_operator_read_allowed('logs.read'));
