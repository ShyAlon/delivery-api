"""Private founder-readable review presentation, no public route or acceptance.

Machine review digest stays a binding, never an owner-readable JSON dump. Typed
artifact is validated against held semantic lineage before summary is returned.
"""
import hashlib,json


def revision_acceptance_view(*,review,review_sha256,artifact,spent_credits):
    if hashlib.sha256(json.dumps(review,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=review_sha256:
        raise ValueError('exact retained installation review required')
    if (review.get('kind')!='closed_revision_installation_review' or review.get('state')!='awaiting_review'
        or any(review.get(k) is not False for k in ('accepted','installed','dispatch_started','installation_available'))):
        raise ValueError('current non-effectful acceptance review required')
    if isinstance(spent_credits,bool) or not isinstance(spent_credits,(int,float)) or spent_credits<0:
        raise ValueError('current nonnegative spent credits required')
    import math
    if not math.isfinite(spent_credits):raise ValueError('finite spent credits required')
    if not isinstance(artifact,dict) or hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=review['regenerated_artifact_sha256']:
        raise ValueError('typed regenerated artifact differs from retained review')
    from vmodel_contracts import SRSDocument,check_compatible
    check_compatible(artifact)
    phase=review['phase']
    if phase=='srs':
        document=SRSDocument.model_validate(artifact)
        if document.session_id!=review['semantic_session_id'] or document.intent_model_ref!=review['semantic_session_id']:
            raise ValueError('acceptance artifact semantic source differs')
        rows=[{'title':r.title,'description':r.statement} for r in document.functional+document.non_functional]
        label='Requirements'
    elif phase=='architecture':
        from spec_generator.graph import validated_architecture
        document=validated_architecture(artifact)
        if document.session_id!=review['semantic_session_id']:raise ValueError('acceptance artifact semantic source differs')
        rows=[{'title':c.name,'description':c.responsibility} for c in document.components]
        label='Architecture'
    else:raise ValueError('implemented acceptance phase required')
    if not rows:raise ValueError('founder-readable regenerated result required')
    from .phases import PHASE_BY_KEY,followers
    next_phase=next(iter(followers(phase)),None)
    return {'binding':{'review_sha256':review_sha256},'view':{'phase_title':label,'task_title':'Regenerated Phase Review',
        'decision':'Decide whether this regenerated result reflects your requested change.',
        'artifact_rows':rows,'next_phase_title':next_phase.label.title() if next_phase else 'Completion',
        'choices':[{'value':'approve','title':'Approve','effect':'Accepts this result as shown and continues to the next phase.'},
            {'value':'edit','title':'Ask For Changes','effect':'Regenerates this phase using your note. Review the new result before moving on.'}],
        'default_choice':'approve','reapproval_titles':[PHASE_BY_KEY[key].label.title() for key in review['phases_requiring_reapproval']],
        'spent_credits':spent_credits,'extra_cost_warning':review['cost_review']['warning'],
        'requires_extra_cost_acknowledgement':True,'previous_generation_preserved':True,'installation_available':False}}
