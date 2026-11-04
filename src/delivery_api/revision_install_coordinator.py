"""Private explicit install coordinator. Unconnected to routes or acceptance UI.

Called only with authenticated exact retained acceptance and original source
writer scope. Never auto-resumes on recovery or dispatches paid work.
"""
import json


def apply_private_installation(store,task_id,*,installation_id,expected_authorized_event_sha256,artifacts_base,validate_acceptance):
    """Hold a PG destination lock across the explicit accepted journal effects."""
    from .pg_revision_install import destination_guard
    with destination_guard(store,task_id,installation_id):
        return _apply_locked_installation(store,task_id,installation_id=installation_id,
            expected_authorized_event_sha256=expected_authorized_event_sha256,artifacts_base=artifacts_base,validate_acceptance=validate_acceptance)


def _apply_locked_installation(store,task_id,*,installation_id,expected_authorized_event_sha256,artifacts_base,validate_acceptance):
    if not callable(validate_acceptance):raise ValueError('trusted exact acceptance verifier required')
    # This private seam uses the owner-scoped transaction rather than accepting
    # a request-supplied plan, file path or review body.
    with store._task_operation_guard('task.rerun'):
        row=store._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
        if not row:raise KeyError(installation_id)
        lineage_row=store._execute_native('SELECT * FROM closed_revision_lineage_plans WHERE lineage_id=%s',(row['lineage_id'],)).fetchone()
        latest=store._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
        if not latest or latest['state']!='authorized' or latest['event_sha256']!=expected_authorized_event_sha256:
            raise ValueError('fresh authorized installation required; explicit reconciliation otherwise')
        lineage=json.loads(lineage_row['payload']);plan=json.loads(row['payload'])
        review=store.retain_revision_installation_review(task_id,lineage_id=row['lineage_id'],
            expected_staged_event_sha256=plan['staged_event_sha256'],artifacts_base=artifacts_base)
        if review['review_sha256']!=row['review_sha256']:raise ValueError('accepted review differs from current install evidence')
        def authority():
            validate_acceptance(review['review'],row['review_sha256'],row['acceptance_reference'])
            if not store.inspect_revision_install_safety(task_id,lineage_id=row['lineage_id'],artifacts_base=artifacts_base)['observed_work_quiescent']:
                raise ValueError('installation source writer/work quiescence unresolved')
        authority()
        from .pg_revision_install import prepare_anchor,read_evidence
        evidence=prepare_anchor(store,task_id,installation_id,authority)
        def staged_evidence():
            authority()
            current=store.retain_revision_installation_review(task_id,lineage_id=row['lineage_id'],expected_staged_event_sha256=plan['staged_event_sha256'],artifacts_base=artifacts_base)
            if current!=review:raise ValueError('stage evidence changed before promotion')
            return read_evidence(store,task_id,installation_id,promoted=False)
        pending=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=latest['event_sha256'],
            state='promotion_pending',evidence=evidence,read_current_evidence=staged_evidence)
    # Persist promotion_pending before the PG receipt. An interruption holds this
    # journal and never enters the authorized-only path again automatically.
    from .pg_revision_install import promote_stage
    promote_stage(store,task_id,installation_id,authority)
    def promoted_evidence():
        authority()
        return store.read_promoted_installation_evidence(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
    promoted=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=pending['event_sha256'],
        state='files_promoted',evidence=evidence,read_current_evidence=promoted_evidence)
    ready=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=promoted['event_sha256'],
        state='install_pending',evidence=evidence,read_current_evidence=promoted_evidence)
    return store.activate_private_revision_installation(task_id,installation_id=installation_id,expected_previous_sha256=ready['event_sha256'],
        validate_acceptance=validate_acceptance,artifacts_base=artifacts_base)
