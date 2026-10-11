"""Private operator snapshot preparation, not a live registry or SQL adapter.

Caller supplies a complete backend-only account registry and provider ledger
snapshot at one as-of instant. No owner/browser API receives these records.
Count eligibility (registered, non-deleted accounts) is an implementation
assumption for owner review. Held attempts never become free or age out.
"""
import math
from .attempt_ledger import AdmissionDenied,_integer
from .expense_alarm_policy import platform_daily_limit


def build_snapshot(*,accounts,attempts,as_of,registry_complete,ledger_complete,policy):
    if registry_complete is not True or ledger_complete is not True:
        raise AdmissionDenied('complete operator registry and expense ledger required')
    if not isinstance(as_of,(int,float)) or isinstance(as_of,bool) or not math.isfinite(as_of) or as_of<0:raise AdmissionDenied('verified snapshot time required')
    owner_ids=set()
    for account in accounts:
        owner=account.get('owner_id')
        if not isinstance(owner,str) or not owner or owner in owner_ids:raise AdmissionDenied('unique account registry identities required')
        owner_ids.add(owner)
        if 'deleted_at' not in account or account.get('registered') is not True:
            raise AdmissionDenied('explicit registered account/deletion status required')
        deleted=account['deleted_at']
        if deleted is not None and (not isinstance(deleted,(int,float)) or isinstance(deleted,bool) or not math.isfinite(deleted) or deleted<0 or deleted>as_of):
            raise AdmissionDenied('invalid account deletion time')
    count=sum(account['deleted_at'] is None for account in accounts)
    sessions={};seen=set();settled=held=0
    for attempt in attempts:
        key=attempt.get('attempt_id');owner=attempt.get('owner_id');session=attempt.get('session_id');state=attempt.get('state')
        if not isinstance(key,str) or not key or key in seen or owner not in owner_ids or not isinstance(session,str) or not session:
            raise AdmissionDenied('complete unique attempt/account/session binding required')
        seen.add(key)
        if state not in ('reserved','dispatched','uncertain','settled','cancelled','failure_waived'):raise AdmissionDenied('unknown attempt state')
        totals=sessions.setdefault((owner,session),{'settled_units':0,'held_units':0,'uncertain_attempts':0})
        if state=='settled':
            amount=attempt.get('expense_actual');ts=attempt.get('settled_at')
            if not _integer(amount) or not isinstance(ts,(int,float)) or isinstance(ts,bool) or not math.isfinite(ts) or ts<0 or ts>as_of:
                raise AdmissionDenied('settled expense and source settlement time required')
            totals['settled_units']+=amount
            if as_of-86400<ts<=as_of:settled+=amount
        elif state in ('reserved','dispatched','uncertain','failure_waived'):
            amount=attempt.get('expense_reserved')
            if not _integer(amount):raise AdmissionDenied('verified outstanding expense hold required')
            held+=amount;totals['held_units']+=amount
            if state=='uncertain':totals['uncertain_attempts']+=1
    limit=platform_daily_limit(policy,count)
    return {'as_of':as_of,'registered_non_deleted_accounts':count,
        'count_eligibility':'implementation assumption: registered non-deleted accounts',
        'platform_rolling_settled_units':settled,'platform_outstanding_hold_units':held,
        'platform_conservative_used_units':settled+held,'platform_limit_units':limit,
        'platform_new_calls_blocked':settled+held>=limit,
        'sessions':[{'owner_id':owner,'session_id':session,**totals,
            'new_calls_blocked':totals['settled_units']+totals['held_units']>=policy.session_units}
            for (owner,session),totals in sorted(sessions.items())],
        'operator_only':True,'installed':False}
