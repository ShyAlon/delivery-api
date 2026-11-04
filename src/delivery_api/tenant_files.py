"""Resolve scratch namespaces only through the RLS ownership registry.

No content readers: durable content belongs to GeneratorDocuments.
"""
from pathlib import Path
from .tenancy import current_scope, session_root, TenantAccessDenied


def authorized_session(base:Path,session_id:str,store=None)->Path:
    if store is None:
        raise TenantAccessDenied('session ownership repository required')
    binding=store.session_binding(session_id)
    scope=current_scope()
    if not binding or str(binding['owner_id'])!=scope.owner_id:
        raise TenantAccessDenied('unknown session')
    root=session_root(base,scope,session_id)
    # Reject unsafe trees even for metadata reads. Content reads additionally
    # use pinned no-follow descriptors and post-read identity checks.
    import os
    if root.exists():
        for directory,dirs,files in os.walk(root,followlinks=False):
            for name in dirs+files:
                if (Path(directory)/name).is_symlink():
                    raise TenantAccessDenied('session contains an unsafe symlink')
    return root
