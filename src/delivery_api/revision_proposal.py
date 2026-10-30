"""Read retained proposal bytes from the captured owner/session namespace.

This authenticates filesystem location and reviewed content, not owner approval.
The proposal identifier must come from the private job result. No installation.
"""
import hashlib,json,os,re,stat
from pathlib import Path
from .producer_inputs import _open_directory,_identity,_content_stamp,MAX_DOCUMENT_BYTES
from .tenancy import session_root,TenantAccessDenied
from .revision_intent import validate_retained_proposal


def read_retained_revision_proposal(store,capability,root,proposal_id,*,artifacts_base,options):
    capability.validate(store)
    if not isinstance(proposal_id,str) or not re.fullmatch('[a-f0-9]{32}',proposal_id):
        raise ValueError('private canonical proposal identifier required')
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('proposal root differs from captured session')
    if hasattr(store,'_postgres'):
        # A retained intent already owns exact proposal bytes/identity. New
        # producer proposals are retained separately before any intent exists.
        proposal=store.decision_review(capability.task_id,'pg-revision-proposal:'+proposal_id,'result')
        if proposal is None:
            rows=store._execute_native('SELECT payload FROM closed_revision_restart_intents WHERE task_id=%s',(capability.task_id,)).fetchall()
            proposals=[json.loads(row['payload'])['proposal'] for row in rows if json.loads(row['payload'])['proposal'].get('proposal_id')==proposal_id]
            if not proposals or any(value!=proposals[0] for value in proposals):raise ValueError('owned exact retained PG proposal required')
            proposal=proposals[0]
        if any(proposal.get(k)!=v for k,v in (('proposal_id',proposal_id),('task_id',capability.task_id),('session_id',capability.session_id),('generation',capability.generation))):
            raise TenantAccessDenied('retained proposal belongs to another worker')
        validate_retained_proposal(options=options,proposal=proposal)
        capability.validate(store)
        return {'proposal':proposal,'document_sha256':hashlib.sha256(json.dumps(proposal,sort_keys=True,indent=2).encode()).hexdigest()}
    directory=root/'spec'/'revision-proposals'/proposal_id
    parent=_open_directory(directory);fd=None
    try:
        fd=os.open('proposal.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_DOCUMENT_BYTES:
            raise ValueError('bounded regular retained proposal required')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(MAX_DOCUMENT_BYTES+1)
        if len(raw)>MAX_DOCUMENT_BYTES or _content_stamp(os.fstat(fd))!=_content_stamp(before):
            raise TenantAccessDenied('proposal content changed during read')
        if _content_stamp(os.stat('proposal.json',dir_fd=parent,follow_symlinks=False))!=_content_stamp(before):
            raise TenantAccessDenied('proposal namespace changed during read')
        proposal=json.loads(raw)
        if not isinstance(proposal,dict) or any(proposal.get(k)!=v for k,v in (
            ('proposal_id',proposal_id),('task_id',capability.task_id),
            ('session_id',capability.session_id),('generation',capability.generation))):
            raise TenantAccessDenied('retained proposal belongs to another worker')
        validate_retained_proposal(options=options,proposal=proposal)
        current=_open_directory(directory)
        try:
            if _identity(current)!=_identity(parent) or _content_stamp(os.stat('proposal.json',dir_fd=current,follow_symlinks=False))!=_content_stamp(before):
                raise TenantAccessDenied('retained proposal namespace changed')
        finally:os.close(current)
        capability.validate(store)
        return {'proposal':proposal,'document_sha256':hashlib.sha256(raw).hexdigest()}
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)
