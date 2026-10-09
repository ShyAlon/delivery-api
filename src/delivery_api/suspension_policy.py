"""Explicit suspension semantics. Pure policy only, no account mutation.

Shy's 2026-10-09 ruling: task operations blocked, communication remains open;
already accepted calls finish and bill. HTTP method is not an action classifier.
"""
TASK_OPERATIONS=frozenset({'task.create','task.edit','task.delete','task.stop',
    'task.retry','task.rerun','task.start','task.answer','task.gate.decide',
    'task.gate.draft','task.decision.comment','task.decision.draft',
    'task.model_route','task.simulate','worker.claim','provider.reserve','provider.dispatch'})
PRESERVED_ACTIONS=frozenset({'auth.signin','auth.refresh','auth.logout',
    'system.communicate','task.read','task.export','provider.complete','provider.settle',
    'provider.mark_uncertain','provider.cancel_reserved','worker.quiesce'})


def action_allowed(*,suspended,action):
    """Fail closed for unclassified actions; no false truthy state coercions."""
    if type(suspended) is not bool:raise ValueError('verified suspension state required')
    if action not in TASK_OPERATIONS|PRESERVED_ACTIONS:
        raise ValueError('explicit reviewed action required')
    return not suspended or action in PRESERVED_ACTIONS


def suspension_proposal(*,account,suspend,reason):
    """Bind current target/status/version for review, not execution authority."""
    import hashlib,json
    from uuid import UUID
    if not isinstance(account,dict) or not {'owner_id','suspended','version'}<=account.keys():
        raise ValueError('complete current account snapshot required')
    owner=account['owner_id'];version=account['version']
    try:
        if str(UUID(owner))!=owner:raise ValueError()
    except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical target account required') from exc
    if type(account['suspended']) is not bool or type(suspend) is not bool:
        raise ValueError('exact suspension state required')
    if type(version) is not int or not 0<=version<=9223372036854775807:
        raise ValueError('exact current policy version required')
    if account['suspended']==suspend:raise ValueError('status already matches requested change')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>500 or any(ord(c)<32 for c in reason):
        raise ValueError('bounded single-line reason required')
    proposal={'kind':'user_suspension_review','policy_version':'task-operations-only-v1',
        'target_owner_id':owner,'before_suspended':account['suspended'],'after_suspended':suspend,
        'expected_version':version,'reason':reason,'blocked_actions':sorted(TASK_OPERATIONS),
        'preserved_actions':sorted(PRESERVED_ACTIONS),'effect_executed':False,'commit_available':False,
        'commit_requirements':['reviewed target status and reason','fresh operator users.suspend capability',
            'transactional policy compare-and-swap and audit','shared owner admission lock',
            'replay-safe request key','no call abortion or automatic restart']}
    proposal['review_digest']=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return proposal
