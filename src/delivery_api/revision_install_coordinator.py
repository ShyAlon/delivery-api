"""Private explicit install coordinator. Unconnected to routes or acceptance UI.

Called only with authenticated exact retained acceptance and original source
writer scope. Never auto-resumes on recovery or dispatches paid work.
"""
import json
from pathlib import Path
from .tenancy import session_root,current_scope
from .revision_install_files import prepare_execution_anchor,promote_private_stage


def apply_private_installation(store,task_id,*,installation_id,expected_authorized_event_sha256,artifacts_base,validate_acceptance):
    """Hold exclusive destination namespace lock across journal/files/DB effects."""
    import os,fcntl
    from .producer_inputs import _open_directory
    with store._task_operation_guard('task.rerun'):
        row=store._execute_native('SELECT * FROM closed_revision_installations WHERE installation_id=%s AND task_id=%s',(installation_id,task_id)).fetchone()
        if not row:raise KeyError(installation_id)
        latest=store._execute_native('SELECT * FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(installation_id,)).fetchone()
        if not latest or latest['state']!='authorized' or latest['event_sha256']!=expected_authorized_event_sha256:
            raise ValueError('fresh authorized installation required; explicit reconciliation otherwise')
        plan=json.loads(row['payload']);sid=plan['destination_anchor']['execution_session_id']
        # Validate exact plan/session before any lock filename is constructed.
        from .revision_install_files import _validate_plan
        _validate_plan(plan,row['plan_sha256'])
        sessions=session_root(Path(artifacts_base),current_scope(),sid).parent
    parent=_open_directory(sessions);fd=None
    try:
        fd=os.open('.install-'+sid+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=parent)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _apply_locked_installation(store,task_id,installation_id=installation_id,
            expected_authorized_event_sha256=expected_authorized_event_sha256,artifacts_base=artifacts_base,validate_acceptance=validate_acceptance)
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


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
        root=session_root(Path(artifacts_base),current_scope(),lineage['parent_execution_session_id'])
        stage=root/'spec'/'revision-proposals'/lineage['proposal_id']/'staged'/lineage['execution_session_id']
        proof=prepare_execution_anchor(stage_root=stage,plan=plan,plan_sha256=row['plan_sha256'],validate_authority=authority)
        evidence={'manifest_sha256':plan['staged_receipt']['manifest_sha256'],'anchor_sha256':proof['anchor_sha256']}
        def staged_evidence():
            authority()
            current=store.retain_revision_installation_review(task_id,lineage_id=row['lineage_id'],expected_staged_event_sha256=plan['staged_event_sha256'],artifacts_base=artifacts_base)
            if current!=review:raise ValueError('stage evidence changed before promotion')
            # Exact fsynced anchor bytes are bound separately from artifact manifest.
            from .producer_inputs import _open_directory
            import os,hashlib
            fd=_open_directory(stage)
            try:
                file=os.open('execution-generation.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    with os.fdopen(file,'rb',closefd=False) as f:raw=f.read(16385)
                finally:os.close(file)
            finally:os.close(fd)
            if len(raw)>16384 or hashlib.sha256(raw).hexdigest()!=evidence['anchor_sha256']:raise ValueError('prepared anchor changed')
            return evidence
        pending=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=latest['event_sha256'],
            state='promotion_pending',evidence=evidence,read_current_evidence=staged_evidence)
    # Persist promotion_pending before rename. An interruption holds this journal
    # and never enters the authorized-only path again automatically.
    promote_private_stage(artifacts_base=artifacts_base,lineage=lineage,plan=plan,plan_sha256=row['plan_sha256'],validate_authority=authority)
    def promoted_evidence():
        authority()
        return store.read_promoted_installation_evidence(task_id,installation_id=installation_id,artifacts_base=artifacts_base)
    promoted=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=pending['event_sha256'],
        state='files_promoted',evidence=evidence,read_current_evidence=promoted_evidence)
    ready=store.append_private_installation_event(task_id,installation_id=installation_id,expected_previous_sha256=promoted['event_sha256'],
        state='install_pending',evidence=evidence,read_current_evidence=promoted_evidence)
    return store.activate_private_revision_installation(task_id,installation_id=installation_id,expected_previous_sha256=ready['event_sha256'],
        validate_acceptance=validate_acceptance,artifacts_base=artifacts_base)
