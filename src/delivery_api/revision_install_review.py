"""Immutable installation review basis, not acceptance or permission to install."""
import hashlib,json,re
from copy import deepcopy


def installation_review_payload(*,lineage,lineage_sha256,proposal_document_sha256,source_snapshot_sha256,
                               staged_receipt,staged_event_sha256):
    digest=lambda body:hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if not isinstance(lineage,dict) or digest(lineage)!=lineage_sha256:raise ValueError('exact held lineage required')
    if (lineage.get('state')!='planned' or lineage.get('installed') is not False or lineage.get('dispatch_started') is not False
        or lineage.get('phase') not in ('srs','architecture') or type(lineage.get('parent_generation')) is not int
        or lineage['parent_generation']<1 or type(lineage.get('generation')) is not int
        or lineage['generation']!=lineage['parent_generation']+1):raise ValueError('uninstalled exact next lineage required')
    for value in (lineage_sha256,proposal_document_sha256,source_snapshot_sha256,staged_event_sha256):
        if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('exact review evidence digests required')
    if proposal_document_sha256!=lineage.get('proposal_document_sha256'):raise ValueError('proposal review differs from lineage')
    if (not isinstance(staged_receipt,dict) or set(staged_receipt)!={'manifest_sha256','checkpoint_id'}
        or not isinstance(staged_receipt['manifest_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',staged_receipt['manifest_sha256'])
        or not isinstance(staged_receipt['checkpoint_id'],str) or not 1<=len(staged_receipt['checkpoint_id'])<=200):
        raise ValueError('exact concrete staged receipt required')
    from .phases import followers
    phase=lineage['phase']
    body={'kind':'closed_revision_installation_review','owner_id':lineage['owner_id'],'task_id':lineage['task_id'],
        'lineage_sha256':lineage_sha256,'semantic_session_id':lineage['semantic_session_id'],
        'parent_execution_session_id':lineage['parent_execution_session_id'],'parent_generation':lineage['parent_generation'],
        'execution_session_id':lineage['execution_session_id'],'generation':lineage['generation'],'phase':phase,
        'proposal_id':lineage['proposal_id'],'proposal_document_sha256':proposal_document_sha256,
        'regenerated_artifact_sha256':lineage['regenerated_artifact_sha256'],
        'source_snapshot_sha256':source_snapshot_sha256,'staged_receipt':deepcopy(staged_receipt),
        'staged_event_sha256':staged_event_sha256,'phases_requiring_reapproval':[phase]+[p.key for p in followers(phase)],
        'preserve_previous_generation':True,'interview_mutation_allowed':False,
        'cost_review':{'warning':'Continuing regenerated downstream work incurs extra cost.',
            'amount_known':False,'requires_explicit_cost_acknowledgement':True,'admission_required_per_call':True},
        'state':'awaiting_review','accepted':False,'installed':False,'dispatch_started':False,
        'automatic_replay_allowed':False,'installation_available':False}
    return {'review':body,'review_sha256':digest(body)}
