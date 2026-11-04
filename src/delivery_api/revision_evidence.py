"""Current owned PostgreSQL phase evidence for private revisions."""
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied


def read_revision_artifacts(store,capability,root,phase,*,artifacts_base):
    capability.validate(store)
    if phase not in ('srs','architecture'):raise ValueError('implemented revision phase required')
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('revision root differs from captured session')
    from .pg_revision_artifacts import phase_evidence
    return phase_evidence(store,capability,phase)
