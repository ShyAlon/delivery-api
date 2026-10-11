"""Validate resolved founder decisions before explicit whole-CONOPS continuation."""
import hashlib,json
from vmodel_contracts import IntentModel
from vmodel_contracts.semantic_review import SemanticReview


def validate_checkpoint(values,review,*,actor,session_id):
    reviewed=SemanticReview.model_validate(review)
    if reviewed.session_id!=session_id or any(d.actor!=actor for d in reviewed.decisions):
        raise ValueError('original founder semantic decisions required')
    if not values.get('semantic_review_resolved') or values.get('semantic_review')!=review:
        raise ValueError('checkpoint semantic decision binding changed')
    resolved=reviewed.resolved_candidate(IntentModel.model_validate)
    if values.get('intent_model')!=resolved:
        raise ValueError('resolved intent differs from approved semantic decisions')
    if values.get('conops') or values.get('conops_draft'):
        raise ValueError('expected failed uncompleted CONOPS node only')
    if values.get('submission',{}).get('submission_id')!=session_id or values.get('verdict',{}).get('accepted') is not True:
        raise ValueError('original submission and accepted scope verdict required')
    if not values.get('messages'):
        raise ValueError('original interview transcript checkpoint required')
    return {'session_id':session_id,'semantic_revision':reviewed.revision,
            'review_sha256':hashlib.sha256(json.dumps(review,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'intent_sha256':hashlib.sha256(json.dumps(resolved,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'next_action':'regenerate-whole-conops','interview_repeated':False,'automatic_approval':False}


def restore_for_conops(program,review,*,actor):
    # Constructor alone does not run graph/provider calls. Saved state is read
    # under the already-bound same-host worker writer fence.
    snapshot=program.graph.get_state(program.cfg)
    evidence=validate_checkpoint(snapshot.values,review,actor=actor,session_id=program.session_id)
    # Explicit update-as-distill pins the next node. No stale input is reused.
    program.graph.update_state(program.cfg,{},as_node='distill_intent')
    program.result=snapshot.values;program.state=None;program.open_input=None
    return evidence
