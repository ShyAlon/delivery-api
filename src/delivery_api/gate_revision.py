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


def closed_revision_preflight(store,task_id,gate_key,review_revision,draft_id,*,artifact_snapshot):
    """Non-effectful review of a saved proposal against current approval/evidence.

    artifact_snapshot must come from current authorized source reads. This function
    does not authenticate a caller's digest, validate regenerated typed outputs, or
    provide a route that commits/restarts work.
    """
    import hashlib,json,re
    from .phases import PHASE_BY_KEY
    initial=store.task(task_id)
    if not initial:raise KeyError(task_id)
    draft=store.decision_draft(task_id,'gate:'+gate_key,review_revision,draft_id)
    if not isinstance(draft,dict) or draft.get('kind')!='closed_gate_revision':
        raise ValueError('saved closed-gate revision proposal required')
    preview=closed_revision_preview(store,task_id,gate_key,review_revision,draft.get('edited_text',''))
    if draft!=preview:raise ValueError('saved proposal basis changed; save a current proposal')
    if not isinstance(artifact_snapshot,list) or not artifact_snapshot:
        raise ValueError('current authorized artifact evidence required')
    task=store.task(task_id)
    normalized=[];names=set()
    for artifact in artifact_snapshot:
        if not isinstance(artifact,dict) or set(artifact)!={'phase','name','generation','sha256'}:
            raise ValueError('exact current artifact evidence required')
        phase,name,generation,digest=(artifact[k] for k in ('phase','name','generation','sha256'))
        if phase!=preview['phase'] or type(generation) is not int or generation!=task['generation']:
            raise ValueError('artifact evidence belongs to another phase or generation')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}',name) or name in names:
            raise ValueError('unique canonical artifact names required')
        if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):
            raise ValueError('current artifact content digest required')
        names.add(name);normalized.append(dict(artifact))
    required=set(PHASE_BY_KEY[preview['phase']].artifacts)
    if not required<=names:raise ValueError('complete canonical phase artifact evidence required')
    # A preflight is not atomic, but cannot knowingly bind a mixed task snapshot.
    if task!=initial or store.task(task_id)!=task:
        raise ValueError('task changed during revision preflight')
    result={'kind':'closed_gate_revision_preflight','task_id':task_id,'generation':task['generation'],
        'gate_key':gate_key,'review_revision':review_revision,'draft_id':draft_id,
        'proposal':preview,'artifact_evidence':sorted(normalized,key=lambda a:a['name']),
        # Save's historical draft schema stays unchanged. Commit semantics live
        # here: founder wording is regeneration input, never replacement JSON.
        'regeneration':{'input_kind':'founder_change_request',
            'change_request':preview['edited_text'],'from_phase':preview['phase'],
            'phases_requiring_reapproval':[preview['phase']]+preview['downstream_stale_on_commit'],
            'direct_artifact_replacement':False,'preserve_previous_generation':True,
            'producer':PHASE_BY_KEY[preview['phase']].executor},
        'cost_review':{'extra_cost_expected':True,'amount_known':False,
            'requires_explicit_cost_acknowledgement':True,
            'warning':'Regenerating this phase and affected downstream work incurs extra cost.',
            'admission_required_per_call':True},
        'commit_available':False,'effect_executed':False,'dispatch_started':False,
        'remaining_requirements':['phase-specific founder-change input and fresh regeneration checkpoint',
            'typed regenerated output and trace-link validation',
            'fresh authority and atomic gate/generation/artifact compare-and-swap',
            'explicit reviewed regenerate-and-restart with extra-cost acknowledgement and admission',
            'durable restart intent and no automatic replay after ambiguity']}
    result['review_digest']=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result
