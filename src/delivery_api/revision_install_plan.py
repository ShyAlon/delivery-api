"""Pure reviewed-installation effect plan, no approval or filesystem/DB effects."""
import hashlib,json,re
from copy import deepcopy


def prepare_installation_plan(*,review,review_sha256,installation_id):
    from vmodel_contracts.execution_anchor import ExecutionAnchor
    from .phases import followers
    digest=lambda body:hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if not isinstance(review,dict) or digest(review)!=review_sha256:
        raise ValueError('exact retained installation review basis required')
    if (review.get('kind')!='closed_revision_installation_review' or review.get('state')!='awaiting_review'
        or any(review.get(k) is not False for k in ('accepted','installed','dispatch_started','installation_available','automatic_replay_allowed','interview_mutation_allowed'))
        or review.get('preserve_previous_generation') is not True):
        raise ValueError('non-effectful installation review required')
    cost=review.get('cost_review')
    if (not isinstance(cost,dict) or cost.get('requires_explicit_cost_acknowledgement') is not True
        or cost.get('admission_required_per_call') is not True or cost.get('amount_known') is not False):
        raise ValueError('explicit continuing cost review required')
    phase=review.get('phase')
    if phase not in ('srs','architecture'):raise ValueError('implemented reviewed phase required')
    affected=[phase]+[p.key for p in followers(phase)]
    if review.get('phases_requiring_reapproval')!=affected:raise ValueError('exact downstream invalidation required')
    if (type(review.get('parent_generation')) is not int or review['parent_generation']<1
        or type(review.get('generation')) is not int or review['generation']!=review['parent_generation']+1):
        raise ValueError('exact next generation required')
    for key in ('lineage_sha256','proposal_document_sha256','regenerated_artifact_sha256','source_snapshot_sha256','staged_event_sha256'):
        if not isinstance(review.get(key),str) or not re.fullmatch('[a-f0-9]{64}',review[key]):raise ValueError('exact review evidence digests required')
    receipt=review.get('staged_receipt')
    if (not isinstance(receipt,dict) or set(receipt)!={'manifest_sha256','checkpoint_id'}
        or not isinstance(receipt['manifest_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',receipt['manifest_sha256'])
        or not isinstance(receipt['checkpoint_id'],str) or not 1<=len(receipt['checkpoint_id'])<=200):
        raise ValueError('exact staged installation evidence required')
    anchor=ExecutionAnchor(review['owner_id'],review['task_id'],review['execution_session_id'],review['semantic_session_id'],
        review['generation'],review['lineage_sha256'],installation_id)
    body={'kind':'closed_revision_installation_effect_plan','review_sha256':review_sha256,'installation_id':installation_id,
        'expected_parent':{'owner_id':review['owner_id'],'task_id':review['task_id'],
            'execution_session_id':review['parent_execution_session_id'],'generation':review['parent_generation']},
        'destination_anchor':anchor.document(),'staged_receipt':deepcopy(receipt),'staged_event_sha256':review['staged_event_sha256'],
        'edited_gate':{'phase':phase,'state':'decided','approval_reset':False,'approved_by_exact_acceptance':True},
        'downstream':{'phases':[p.key for p in followers(phase)],'approval_reset':True,'state':'pending'},
        'preserve_previous_generation':True,'source_deletion_allowed':False,'copy_stop_marker':False,
        'state':'planned','accepted':False,'installed':False,'dispatch_started':False,'automatic_replay_allowed':False,
        'requirements':['authenticated owner review acceptance and cost acknowledgement','current owner policy and exact gate/draft/source/proposal/stage evidence',
            'same-host source/destination writer exclusion and provider/worker quiescence','fsynced private-to-owned destination promotion with no overwrite',
            'durable promotion/install journal and database parent-generation CAS','installed anchor/session binding readback before explicit continuation']}
    return {'plan':body,'plan_sha256':digest(body)}
