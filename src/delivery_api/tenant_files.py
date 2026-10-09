"""Resolve cloud session files only through the RLS ownership registry."""
from pathlib import Path
from . import config
from .tenancy import current_scope, session_root, TenantAccessDenied


def authorized_session(base:Path,session_id:str,store=None)->Path:
    if not config.CLOUD_TENANTS:
        return base/session_id
    if store is None:
        raise TenantAccessDenied('session ownership repository required')
    binding=store.session_binding(session_id)
    scope=current_scope()
    if not binding or str(binding['owner_id'])!=scope.owner_id:
        raise TenantAccessDenied('unknown session')
    root=session_root(base,scope,session_id)
    # Until safe descriptor-based reads exist, reject the whole session if any
    # internal symlink could let existing exporters/readers leave its namespace.
    import os
    if root.exists():
        for directory,dirs,files in os.walk(root,followlinks=False):
            for name in dirs+files:
                if (Path(directory)/name).is_symlink():
                    raise TenantAccessDenied('session contains an unsafe symlink')
    return root
