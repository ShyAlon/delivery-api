"""Starting the phase after the interview for a cloud account: a click, never automatic.

Pure rules, so each one is tested without a server. Everything a customer reads here is in credits."""
from decimal import Decimal,InvalidOperation

PHASE='needs'


def eligible(task):
    """The interview is finished and approved, nothing is running, and the phase has not started."""
    if not isinstance(task,dict) or task.get('state')!='running' or task.get('active_phase')!='conops':return False
    if task.get('stop_requested_at') is not None:return False
    states={p.get('phase_key'):p.get('state') for p in task.get('phases') or []}
    return states.get('intent') in ('approved','done') and states.get('conops') in ('approved','done') and states.get(PHASE)=='pending'


def _credits(value):
    try:return Decimal(str(value))
    except (InvalidOperation,ValueError):return None


def _show(value):
    return f'{value.quantize(Decimal("0.01")):,}'


def credit_check(credits_left,minimum):
    """Whether the owner has enough credits to start, with the plain-language message for the page."""
    minimum=Decimal(str(minimum));have=_credits(credits_left)
    floor=f'{minimum.normalize():f}'
    if have is None:
        return {'can_start':False,'credits_left':None,'minimum_credits':float(minimum),
                'message':'Your credit balance could not be read. Refresh the page and try again.'}
    if have<minimum:
        return {'can_start':False,'credits_left':float(have),'minimum_credits':float(minimum),
                'message':f'You have {_show(have)} credits. Starting this phase needs at least {floor}. Add credits, then start it.'}
    return {'can_start':True,'credits_left':float(have),'minimum_credits':float(minimum),
            'message':f'This phase uses your credits as it runs. You have {_show(have)} credits left. You can stop it at any time.'}
