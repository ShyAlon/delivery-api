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



def _content_stamp(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def read_fresh_inputs(store,capability,root,*,artifacts_base):
    """No client path, global baseline, inferred greenfield or contract restamp.

    Input bytes must already exist in the owned ready PG manifest. The graph owns
    schema/compatibility/confirmed-concept checks before creating spec output.
    """
    capability.validate(store)
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('producer input root differs from owned session')
    snapshot=store.manifest_snapshot(capability.task_id,capability.generation)
    if snapshot is None:raise ValueError('owned ready input manifest required')
    members={(row['phase_key'],row['name']):row for row in snapshot['rows']};result={}
    for key,phase,name in [('intent','intent','intent_model.json'),('conops','conops','conops.json'),('baseline','baseline','system-baseline.json')]:
        member=members.get((phase,name))
        if member is None:raise ValueError('owned producer input missing')
        raw=store.read_artifact_bytes_sha(member['sha256'])
        if len(raw)>MAX_DOCUMENT_BYTES:raise ValueError('bounded producer input required')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('producer input document object required')
        result[key]=value
    capability.validate(store);return result
