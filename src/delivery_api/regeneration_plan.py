"""Code-owned phase/change-input map. No producer or dispatch authority.

Current support means source code exists at the named boundary, not that a closed
phase can already commit. The lifecycle must keep this distinction explicit.
"""
from .phases import PHASE_BY_KEY


def phase_change_plan(phase):
    definition=PHASE_BY_KEY.get(phase)
    if definition is None:raise ValueError('canonical edited phase required')
    if phase=='needs':
        return {'phase':phase,'producer_stage':'needs','generation_kind':'deterministic_from_intent',
            'existing_change_boundary':None,'closed_change_adapter_available':False,
            'missing_capabilities':['revise and re-approve upstream IntentModel stakeholder needs before deterministic needs generation'],
            'must_not_claim_guidance_applied_without_upstream_change':True}
    if phase=='intent':
        missing=['revision-bound interview source-change and fresh distillation input']
        boundary=None
    elif phase=='conops':
        missing=['closed-generation adapter for interview ConOps rework with reviewed source intent']
        boundary='active_interview_approval_rework'
    elif phase in ('srs','architecture'):
        missing=['closed-generation adapter for phase-scoped founder feedback and fresh checkpoint']
        boundary='active_generator_gate_feedback'
    else:
        missing=['phase-scoped founder guidance consumption in generator and fresh partial/checkpoint state']
        boundary=None
    return {'phase':phase,'producer_stage':definition.generator_stage or phase,
        'generation_kind':'model_authoring','existing_change_boundary':boundary,
        'closed_change_adapter_available':False,'missing_capabilities':missing,
        'must_not_claim_guidance_applied_without_upstream_change':False}
