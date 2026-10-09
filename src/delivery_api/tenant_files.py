"""Resolve cloud session files only through the RLS ownership registry."""
from pathlib import Path
from contextvars import ContextVar
from contextlib import contextmanager
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
    # Reject unsafe trees even for metadata reads. Content reads additionally
    # use pinned no-follow descriptors and post-read identity checks.
    import os
    if root.exists():
        for directory,dirs,files in os.walk(root,followlinks=False):
            for name in dirs+files:
                if (Path(directory)/name).is_symlink():
                    raise TenantAccessDenied('session contains an unsafe symlink')
    return root


def read_session_bytes(base:Path,session_id:str,relative:str,store,*,max_bytes=16*1024*1024)->bytes:
    """Owner-bound, no-follow read. Never hand a checked path to a later opener."""
    import os,stat
    from .producer_inputs import _open_directory,_identity,_content_stamp
    parts=Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ('.','..') for p in parts):
        raise TenantAccessDenied('canonical relative session file required')
    root=authorized_session(base,session_id,store)
    try:root_fd=_open_directory(root)
    except OSError as exc:raise TenantAccessDenied('session namespace unavailable') from exc
    directory=os.dup(root_fd)
    file_fd=None
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        file_fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        before=os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>max_bytes:
            raise TenantAccessDenied('bounded regular session file required')
        with os.fdopen(file_fd,'rb',closefd=False) as stream:data=stream.read(max_bytes+1)
        if len(data)>max_bytes or _content_stamp(os.fstat(file_fd))!=_content_stamp(before):
            raise TenantAccessDenied('session file changed during read')
        check_root=_open_directory(authorized_session(base,session_id,store))
        check=os.dup(check_root)
        try:
            if _identity(check_root)!=_identity(root_fd):raise TenantAccessDenied('session namespace changed')
            for part in parts[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=check)
                os.close(check);check=child
            current=os.stat(parts[-1],dir_fd=check,follow_symlinks=False)
            if _identity(check)!=_identity(directory) or _content_stamp(current)!=_content_stamp(before):
                raise TenantAccessDenied('session file namespace changed')
        finally:os.close(check);os.close(check_root)
        return data
    except OSError as exc:
        raise TenantAccessDenied('session file unavailable or unsafe') from exc
    finally:
        if file_fd is not None:os.close(file_fd)
        os.close(directory);os.close(root_fd)


_reader:ContextVar[object]=ContextVar('owner_file_reader',default=None)


@contextmanager
def session_reader(base:Path,session_id:str,store):
    """Bind existing JSON/text readers to an owner namespace, including threads."""
    root=authorized_session(base,session_id,store)
    token=_reader.set((base,session_id,store,root))
    try:yield root
    finally:_reader.reset(token)


def read_text(path:Path,*,errors='strict')->str:
    reader=_reader.get()
    if reader is None:return path.read_text(errors=errors)
    base,session,store,root=reader
    try:relative=path.relative_to(root)
    except ValueError as exc:raise TenantAccessDenied('read outside bound session') from exc
    return read_session_bytes(base,session,str(relative),store).decode('utf-8',errors=errors)
