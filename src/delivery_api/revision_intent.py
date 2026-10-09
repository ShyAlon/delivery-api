"""Held restart-intent comparison, never a task generation or dispatch commit."""
import hashlib,json,re
from copy import deepcopy
from .closed_revision_job import _options


def intent_payload(*,options,proposal,approval_reference):
    options=_options(options)
    if not isinstance(approval_reference,str) or not approval_reference.strip() or len(approval_reference)>500:
        raise ValueError('reviewed owner approval reference required')
    required={'proposal_id','task_id','session_id','generation','source_checkpoint_id','proposal_checkpoint_id',
        'phase','change_request','artifact_sha256','typed_and_trace_validated','requires_fresh_review',
        'founder_accepted','committed','dispatch_started','artifact'}
    if not isinstance(proposal,dict) or not required<=proposal.keys():raise ValueError('complete retained proposal required')
    if (proposal['phase']!=options['phase'] or proposal['change_request']!=options['change_request']
        or proposal['source_checkpoint_id']!=options['source_checkpoint_id']):
        raise ValueError('proposal differs from reviewed restart options')
    for key in ('proposal_id','task_id','session_id','source_checkpoint_id','proposal_checkpoint_id'):
        if not isinstance(proposal[key],str) or not 1<=len(proposal[key])<=200:raise ValueError('exact proposal identity required')
    if type(proposal['generation']) is not int or proposal['generation']<1:raise ValueError('exact proposal generation required')
    if any(item['generation']!=proposal['generation'] for item in options['artifact_evidence']):
        raise ValueError('proposal artifact generation mismatch')
    if (proposal['typed_and_trace_validated'] is not True or proposal['requires_fresh_review'] is not True
        or any(proposal[k] is not False for k in ('founder_accepted','committed','dispatch_started'))):
        raise ValueError('uncommitted validated proposal required')
    if not isinstance(proposal['artifact_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',proposal['artifact_sha256']):
        raise ValueError('exact regenerated artifact digest required')
    if not isinstance(proposal['artifact'],dict):raise ValueError('retained regenerated artifact required')
    artifact_digest=hashlib.sha256(json.dumps(proposal['artifact'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if artifact_digest!=proposal['artifact_sha256']:raise ValueError('retained artifact digest mismatch')
    body={'kind':'held_closed_revision_restart','options':deepcopy(options),'proposal':deepcopy(proposal),
        'approval_reference':approval_reference,'extra_cost_acknowledged':True,
        'state':'held','generation_installed':False,'dispatch_started':False,
        'remaining_requirements':['atomic filesystem promotion with current checkpoint/artifact evidence',
            'reviewed new session/generation binding and no automatic replay after ambiguity']}
    raw=json.dumps(body,sort_keys=True,separators=(',',':'))
    return body,hashlib.sha256(raw.encode()).hexdigest()
