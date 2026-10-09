"""Read-only canonical owner inputs for the private same-host producer."""
import json,os,stat
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied

MAX_DOCUMENT_BYTES=2*1024*1024


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
    for key,relative in (('intent','interview/intent_model.json'),('conops','interview/conops.json'),('baseline','spec/system-baseline.json')):
        path=root/relative
        if path.resolve()!=path:raise TenantAccessDenied('unsafe producer input path')
        # O_NOFOLLOW guards replacement of the final file with a symlink.
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_DOCUMENT_BYTES:
                raise ValueError('bounded regular producer input file required')
            with os.fdopen(fd,'rb',closefd=False) as stream:
                data=stream.read(MAX_DOCUMENT_BYTES+1)
            if len(data)>MAX_DOCUMENT_BYTES:raise ValueError('producer input grew beyond bound')
            value=json.loads(data)
            if not isinstance(value,dict):raise ValueError('producer input document object required')
            result[key]=value
        finally:os.close(fd)
    capability.validate(store)
    return result
