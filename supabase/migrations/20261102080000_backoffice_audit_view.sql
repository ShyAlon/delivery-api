-- Read-only curated operations audit, under the existing reviewed logs grant.
-- No new cross-owner mutation, arbitrary audit payload disclosure or write-off.
CREATE POLICY operator_curated_audit_read ON public.audit_log FOR SELECT TO requi_operator_read
 USING (action IN ('provider.attempt.uncertain','provider.attempt.cancelled','provider.attempt.released','provider.attempt.release_failed','task.deleted','task.operator_stop_requested','run.stopped','compute.segment_closed_no_provider_call','compute.segment_released_by_owner','compute.segment_reconciled_no_charge','audit.resolution')
 AND public.requi_operator_read_allowed('logs.read'));
