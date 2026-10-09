"""Private installation-only journal chain, no effect invocation or auto replay."""
import hashlib,json,re
from copy import deepcopy

NEXT={'authorized':{'promotion_pending','held_for_review'},'promotion_pending':{'files_promoted','held_for_review'},
    'files_promoted':{'install_pending','held_for_review'},'install_pending':{'installed','held_for_review'},
    'installed':set(),'held_for_review':set()}


def install_event(*,plan_sha256,previous,state,evidence):
    digest=lambda body:hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    sha=lambda value:isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value)
    if not sha(plan_sha256):raise ValueError('exact installation plan digest required')
    if state not in NEXT:raise ValueError('private installation journal state required')
    if previous is None:
        if state!='authorized':raise ValueError('installation journal begins authorized')
        sequence=0;parent=None
    else:
        if (not isinstance(previous,dict) or set(previous)!={'event','event_sha256'} or digest(previous['event'])!=previous['event_sha256']
            or previous['event']['plan_sha256']!=plan_sha256 or state not in NEXT[previous['event']['state']]):
            raise ValueError('installation journal transition or digest mismatch')
        sequence=previous['event']['sequence']+1;parent=previous['event_sha256']
    required={'authorized':{'review_sha256','acceptance_reference'},'promotion_pending':{'manifest_sha256','anchor_sha256'},
        'files_promoted':{'manifest_sha256','anchor_sha256'},'install_pending':{'manifest_sha256','anchor_sha256'},
        'installed':{'manifest_sha256','anchor_sha256','execution_session_id'},'held_for_review':{'reason'}}[state]
    if not isinstance(evidence,dict) or set(evidence)!=required or any(not isinstance(v,str) or not v.strip() or len(v)>500 for v in evidence.values()):
        raise ValueError('exact bounded installation journal evidence required')
    for key,value in evidence.items():
        if key.endswith('_sha256') and not sha(value):raise ValueError('exact installation evidence digest required')
        if previous and key in ('manifest_sha256','anchor_sha256'):
            prior=previous['event']['evidence'].get(key)
            if prior is not None and prior!=value:raise ValueError('installation file evidence changed across journal')
    body={'kind':'closed_revision_install_journal','plan_sha256':plan_sha256,'sequence':sequence,'previous_sha256':parent,
        'state':state,'evidence':deepcopy(evidence),'dispatch_started':False,'automatic_replay_allowed':False}
    return {'event':body,'event_sha256':digest(body)}
