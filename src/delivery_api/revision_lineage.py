"""Private immutable semantic/execution lineage plan. No session install or I/O."""
import hashlib,json,re
from copy import deepcopy
from .revision_intent import validate_retained_proposal


def plan_revision_lineage(*,capability,semantic_session_id,new_execution_session_id,
                          options,retained_proposal):
    def identity(value):
        return isinstance(value,str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,127}',value)
    if not identity(semantic_session_id) or not identity(new_execution_session_id):
        raise ValueError('canonical explicit semantic and execution identities required')
    if new_execution_session_id in (capability.session_id,semantic_session_id):
        raise ValueError('fresh execution namespace required')
    if type(capability.generation) is not int or capability.generation<1:
        raise ValueError('exact parent generation required')
    if not isinstance(retained_proposal,dict) or set(retained_proposal)!={'proposal','document_sha256'}:
        raise ValueError('trusted retained proposal read required')
    digest=retained_proposal['document_sha256']
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('exact retained proposal document digest required')
    proposal=validate_retained_proposal(options=options,proposal=retained_proposal['proposal'])
    if any(proposal[key]!=value for key,value in (
        ('task_id',capability.task_id),('session_id',capability.session_id),('generation',capability.generation))):
        raise ValueError('proposal parent execution binding mismatch')
    artifact=proposal['artifact']
    if artifact.get('session_id')!=semantic_session_id:
        raise ValueError('regenerated artifact semantic identity mismatch')
    if options['phase']=='srs' and artifact.get('intent_model_ref')!=semantic_session_id:
        raise ValueError('regenerated SRS semantic trace mismatch')
    # Caller obtains semantic identity from verified upstream evidence/lineage,
    # never from an HTTP payload or the latest execution-session identifier.
    body={'kind':'closed_revision_execution_lineage','owner_id':capability.scope.owner_id,
        'task_id':capability.task_id,'semantic_session_id':semantic_session_id,
        'parent_execution_session_id':capability.session_id,'parent_generation':capability.generation,
        'execution_session_id':new_execution_session_id,'generation':capability.generation+1,
        'phase':options['phase'],'source_checkpoint_id':proposal['source_checkpoint_id'],
        'proposal_id':proposal['proposal_id'],'proposal_checkpoint_id':proposal['proposal_checkpoint_id'],
        'proposal_document_sha256':digest,'regenerated_artifact_sha256':proposal['artifact_sha256'],
        'approved_source_artifact_evidence':deepcopy(options['artifact_evidence']),
        'review_digest':options['preflight_digest'],'state':'planned',
        'installed':False,'dispatch_started':False}
    return {'lineage':body,'lineage_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
