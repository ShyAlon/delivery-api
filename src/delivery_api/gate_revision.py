"""Closed-gate proposed revision preparation. No dispatch or artifact rewrite.

Save is a draft only; explicit commit/restart integration is deliberately absent.
A caller cannot silently invalidate approved/downstream state by saving wording.
"""
from .phases import followers


def closed_revision_preview(store,task_id,gate_key,review_revision,edited_text):
    task=store.task(task_id)
    if not task:raise KeyError(task_id)
    if task.get("state") in ("stop_requested","stopped"):raise ValueError("stopped task revisions are disabled")
    gate=next((g for g in task['gates'] if g['gate_key']==gate_key),None)
    if not gate:raise KeyError(gate_key)
    if gate['state'] not in ('decided','bypassed'):
        raise ValueError('an approved closed gate is required')
    review=store.decision_review(task_id,'gate:'+gate_key,review_revision)
    if review is None:raise ValueError('retained approved review required')
    if review.get("task",{}).get("generation")!=task.get("generation"):
        raise ValueError("approved review belongs to another generation")
    reviewed_gate=next((g for g in review.get('task',{}).get('gates',[]) if g.get('gate_key')==gate_key),None)
    # Review is normally captured while waiting; approval increments once.
    if not reviewed_gate or reviewed_gate.get('state')!='waiting' or reviewed_gate.get('revision')+1!=gate['revision']:
        raise ValueError('review does not match this approved gate revision')
    text=edited_text.strip()
    if not text:raise ValueError('proposed edited text is required')
    phase=gate['after_phase'];later=[p.key for p in followers(phase)]
    return {'kind':'closed_gate_revision','gate_key':gate_key,'gate_revision':gate['revision'],
            'review_revision':review_revision,'phase':phase,'edited_text':text,
            'downstream_stale_on_commit':later,'restart_phase_on_commit':later[0] if later else None,
            'requires_explicit_approve_and_restart':True}


def save_closed_revision(store,task_id,gate_key,review_revision,edited_text):
    preview=closed_revision_preview(store,task_id,gate_key,review_revision,edited_text)
    draft=store.save_decision_draft(task_id,'gate:'+gate_key,review_revision,preview)
    return {'draft_id':draft,'preview':preview,'committed':False,'dispatch_started':False}
