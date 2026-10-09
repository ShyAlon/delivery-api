"""Pure promotion-journal transition validation, not publication or dispatch.

Immutable events form a digest-linked chain. Filesystem and database observations
are separate evidence. Any uncertain install requires explicit reconciliation.
"""
import hashlib,json,re
from copy import deepcopy

STATES=('prepared','staged','promotion_pending','files_promoted','install_pending','installed','held_for_review')
NEXT={'prepared':{'staged','held_for_review'},'staged':{'promotion_pending','held_for_review'},
    'promotion_pending':{'files_promoted','held_for_review'},'files_promoted':{'install_pending','held_for_review'},
    'install_pending':{'installed','held_for_review'},'installed':set(),'held_for_review':set()}


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _sha(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value)


def journal_event(*,lineage_sha256,previous,state,evidence):
    if not _sha(lineage_sha256):raise ValueError('exact lineage digest required')
    if state not in STATES or not isinstance(evidence,dict):raise ValueError('exact journal state/evidence required')
    if previous is None:
        if state!='prepared':raise ValueError('journal begins prepared')
        sequence=0;parent=None
    else:
        if not isinstance(previous,dict) or set(previous)!={'event','event_sha256'} or _digest(previous['event'])!=previous['event_sha256']:
            raise ValueError('valid prior immutable journal event required')
        old=previous['event']
        if old['lineage_sha256']!=lineage_sha256 or state not in NEXT[old['state']]:
            raise ValueError('journal lineage/transition mismatch')
        sequence=old['sequence']+1;parent=previous['event_sha256']
    required={'prepared':{'intent_id','lineage_id'},'staged':{'manifest_sha256','checkpoint_id'},
        'promotion_pending':{'manifest_sha256'},'files_promoted':{'manifest_sha256'},
        'install_pending':{'manifest_sha256','review_reference'},
        'installed':{'manifest_sha256','installation_id'},'held_for_review':{'reason'}}[state]
    if set(evidence)!=required or any(not isinstance(v,str) or not v.strip() or len(v)>500 for v in evidence.values()):
        raise ValueError('exact bounded journal evidence required')
    if 'manifest_sha256' in evidence:
        if not _sha(evidence['manifest_sha256']):raise ValueError('exact staged manifest digest required')
        if previous and 'manifest_sha256' in previous['event']['evidence'] and evidence['manifest_sha256']!=previous['event']['evidence']['manifest_sha256']:
            raise ValueError('staged manifest changed across journal')
    body={'kind':'closed_revision_promotion_journal','lineage_sha256':lineage_sha256,
        'sequence':sequence,'previous_sha256':parent,'state':state,'evidence':deepcopy(evidence),
        'dispatch_started':False}
    return {'event':body,'event_sha256':_digest(body)}


def reconcile_promotion(*,state,files_match_manifest,database_matches_installation):
    if state not in STATES or type(files_match_manifest) is not bool or type(database_matches_installation) is not bool:
        raise ValueError('exact current reconciliation evidence required')
    if state=='installed' and files_match_manifest and database_matches_installation:
        result='installed_verified_no_dispatch'
    elif database_matches_installation and not files_match_manifest:
        result='database_installed_files_mismatch_review'
    elif files_match_manifest and not database_matches_installation:
        result='files_present_installation_unverified_review'
    else:result='held_state_requires_review'
    return {'state':result,'automatic_retry':False,'automatic_cleanup':False,'dispatch_started':False}
