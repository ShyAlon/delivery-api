"""Private installed-approved checkpoint preparation, never dispatch.

Original reviewed checkpoint is immutable. Partial private destination remains
for reconciliation and refuses retries. No route imports this module.
"""
def prepare_private_approved_continuation(store,task_id,*,installation_id,artifacts_base):
    from .pg_revision_continuation import prepare
    return prepare(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base)


def reconcile_private_approved_continuation(store,task_id,*,installation_id,artifacts_base,expected_job_id=None):
    """Read-only private receipt/file/DB comparison. Never retries or dispatches."""
    from .pg_revision_continuation import reconcile
    return reconcile(store,task_id,installation_id=installation_id,artifacts_base=artifacts_base,expected_job_id=expected_job_id)
