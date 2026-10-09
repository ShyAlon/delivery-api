"""Read-only canonical owner inputs for the private same-host producer."""
import json,os,stat,errno
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied

MAX_DOCUMENT_BYTES=2*1024*1024


def _open_directory(path):
    """Walk from filesystem root with no-follow descriptors, never path reopen."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise TenantAccessDenied('absolute canonical directory required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.ELOOP,errno.ENOTDIR):raise TenantAccessDenied('unsafe producer directory') from exc
        raise
    except BaseException:
        os.close(fd);raise


def _identity(fd):
    info=os.fstat(fd);return info.st_dev,info.st_ino



def read_fresh_inputs(store,capability,root,*,artifacts_base):
    """No client path, global baseline, inferred greenfield or contract restamp.

    Input files must already exist under the captured session. The graph owns
    schema/compatibility/confirmed-concept checks before creating spec output.
    """
    capability.validate(store)
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('producer input root differs from owned session')
    result={}
    root_fd=_open_directory(root)
    try:
        for key,directory,filename in (('intent','interview','intent_model.json'),('conops','interview','conops.json'),('baseline','spec','system-baseline.json')):
            parent_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root_fd)
            try:
                fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent_fd)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_DOCUMENT_BYTES:
                        raise ValueError('bounded regular producer input file required')
                    with os.fdopen(fd,'rb',closefd=False) as stream:data=stream.read(MAX_DOCUMENT_BYTES+1)
                    if len(data)>MAX_DOCUMENT_BYTES:raise ValueError('producer input grew beyond bound')
                    value=json.loads(data)
                    if not isinstance(value,dict):raise ValueError('producer input document object required')
                    # A changed namespace cannot be accepted as the original input.
                    check_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root_fd)
                    try:
                        current=os.stat(filename,dir_fd=check_fd,follow_symlinks=False)
                        if _identity(check_fd)!=_identity(parent_fd) or (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):
                            raise TenantAccessDenied('producer input namespace changed during read')
                    finally:os.close(check_fd)
                    result[key]=value
                finally:os.close(fd)
            finally:os.close(parent_fd)
        check_root=_open_directory(root)
        try:
            if _identity(check_root)!=_identity(root_fd):raise TenantAccessDenied('producer session namespace changed during read')
        finally:os.close(check_root)
        capability.validate(store)
        return result
    except OSError as exc:
        if exc.errno in (errno.ELOOP,errno.ENOTDIR):
            raise TenantAccessDenied('unsafe producer directory or file') from exc
        raise
    finally:os.close(root_fd)
