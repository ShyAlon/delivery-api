"""Pure credit-allocation review preparation. No SQL, endpoint or commit authority.

Caller must obtain an authorized, current account snapshot. A proposal digest
binds parameters for review; it is not a signature, grant or replay token.
"""
import hashlib,json,re
from uuid import UUID
MAX_BIGINT=9223372036854775807


def allocation_proposal(*,account,credits,reason):
    if not isinstance(account,dict) or not {'owner_id','balance_units','units_per_credit','updated_at'}<=set(account):
        raise ValueError('complete current account snapshot required')
    owner=account['owner_id']
    try:
        if str(UUID(owner))!=owner:raise ValueError()
    except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical target account required') from exc
    balance=account['balance_units'];scale=account['units_per_credit'];updated=account['updated_at']
    if type(balance) is not int or not -MAX_BIGINT-1<=balance<=MAX_BIGINT or type(scale) is not int or not 1<=scale<=MAX_BIGINT:
        raise ValueError('exact database fixed-point account required')
    import math
    if type(updated) not in (int,float) or not math.isfinite(updated) or updated<0:
        raise ValueError('verified account version timestamp required')
    if not isinstance(credits,str) or len(credits)>80 or not re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?',credits):
        raise ValueError('exact positive decimal credit amount required')
    from decimal import Decimal,localcontext
    with localcontext() as context:
        context.prec=200
        amount=Decimal(credits)*scale
        if amount!=amount.to_integral_value() or not 0<amount<=MAX_BIGINT:
            raise ValueError('credit amount must be positive, representable exactly and bounded')
        delta=int(amount)
    after=balance+delta
    if not -MAX_BIGINT-1<=after<=MAX_BIGINT:raise ValueError('resulting balance exceeds database range')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>500 or any(ord(c)<32 for c in reason):
        raise ValueError('bounded single-line allocation reason required')
    proposal={'kind':'credit_allocation_review','target_owner_id':owner,'amount_credits':credits,
        'delta_units':str(delta),'units_per_credit':str(scale),'before_balance_units':str(balance),
        'after_balance_units':str(after),'expected_account_updated_at':updated,'reason':reason,
        'money_effect_executed':False,'commit_available':False,
        'review_required':['target account','credit amount','resulting balance','reason'],
        'commit_requirements':['source-grounded owner approval','fresh authorization and account compare-and-swap',
            'transactional adjustment ledger and audit','idempotency and concurrency validation']}
    proposal['review_digest']=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return proposal
