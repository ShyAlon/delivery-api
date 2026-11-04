"""Read retained proposal bytes from the captured owner/session namespace.

This authenticates retained PG identity and content, not owner approval.
The proposal identifier must come from the private job result. No installation.
"""
import hashlib,json,re
from pathlib import Path
from .tenancy import session_root,TenantAccessDenied
from .revision_intent import validate_retained_proposal


def read_retained_revision_proposal(store,capability,root,proposal_id,*,artifacts_base,options):
    capability.validate(store)
    if not isinstance(proposal_id,str) or not re.fullmatch('[a-f0-9]{32}',proposal_id):
        raise ValueError('private canonical proposal identifier required')
    root=Path(root)
    if root!=session_root(artifacts_base,capability.scope,capability.session_id):
        raise TenantAccessDenied('proposal root differs from captured session')
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
