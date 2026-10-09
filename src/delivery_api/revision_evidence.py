"""Descriptor-pinned current phase evidence for private revision workflows.

Reads are not an atomic DB/filesystem CAS. The caller must hold the same-host
writer/owner guard for a commit and compare this evidence again inside it.
"""
import hashlib,os,stat
from pathlib import Path
from .producer_inputs import _open_directory,_identity,_content_stamp,MAX_DOCUMENT_BYTES
from .phases import PHASE_BY_KEY
from .tenancy import session_root,TenantAccessDenied


def read_revision_artifacts(store,capability,root,phase,*,artifacts_base):
    capability.validate(store)
    if phase not in ('srs','architecture'):raise ValueError('implemented revision phase required')
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('revision root differs from captured session')
    root_fd=_open_directory(root);parent_fd=None
    try:
        parent_fd=os.open('spec',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root_fd)
        evidence=[];stamps=[]
        for name in PHASE_BY_KEY[phase].artifacts:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent_fd)
            try:
                before=os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_DOCUMENT_BYTES:
                    raise ValueError('bounded regular phase artifact required')
                with os.fdopen(fd,'rb',closefd=False) as file:data=file.read(MAX_DOCUMENT_BYTES+1)
                if len(data)>MAX_DOCUMENT_BYTES or _content_stamp(os.fstat(fd))!=_content_stamp(before):
                    raise TenantAccessDenied('phase artifact changed during evidence read')
                if _content_stamp(os.stat(name,dir_fd=parent_fd,follow_symlinks=False))!=_content_stamp(before):
                    raise TenantAccessDenied('phase artifact namespace changed')
                stamps.append((name,_content_stamp(before)))
                evidence.append({'phase':phase,'name':name,'generation':capability.generation,
                    'sha256':hashlib.sha256(data).hexdigest()})
            finally:os.close(fd)
        for name,stamp in stamps:
            if _content_stamp(os.stat(name,dir_fd=parent_fd,follow_symlinks=False))!=stamp:
                raise TenantAccessDenied('phase artifact changed across evidence snapshot')
        current_root=_open_directory(root)
        try:
            current_parent=os.open('spec',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current_root)
            try:
                if _identity(current_root)!=_identity(root_fd) or _identity(current_parent)!=_identity(parent_fd):
                    raise TenantAccessDenied('revision evidence namespace changed')
            finally:os.close(current_parent)
        finally:os.close(current_root)
        capability.validate(store)
        return evidence
    finally:
        if parent_fd is not None:os.close(parent_fd)
        os.close(root_fd)
