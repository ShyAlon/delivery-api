"""Pure expense alarm policy preparation. Not installed in live admission.

The unresolved overall scope/day mechanics are explicit required configuration,
not inferred from 'per registered user per day'. Amounts are exact fixed point.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from .attempt_ledger import AdmissionDenied


@dataclass(frozen=True)
class ExpenseAlarmPolicy:
    session_units: int
    daily_per_registered_user_units: int
    units_per_usd: int
    overall_scope: str
    day_mode: str


def from_environment(environ):
    try:
        scale = int(environ['VMODEL_EXPENSE_UNITS_PER_USD'])
        if scale <= 0:raise ValueError()
        amounts=[]
        for key in ('VMODEL_SESSION_EXPENSE_LIMIT_USD', 'VMODEL_DAILY_EXPENSE_PER_REGISTERED_USER_USD'):
            dollars=Decimal(environ[key])
            scaled=dollars*scale
            if not dollars.is_finite() or dollars <= 0 or scaled != scaled.to_integral_value():raise ValueError()
            amounts.append(int(scaled))
        scope=environ['VMODEL_OVERALL_EXPENSE_SCOPE']
        day=environ['VMODEL_EXPENSE_DAY_MODE']
        if scope not in ('platform_registered_users','per_owner'):raise ValueError()
        if day not in ('rolling_24h','jerusalem_calendar_day'):raise ValueError()
        return ExpenseAlarmPolicy(*amounts,scale,scope,day)
    except (KeyError,ValueError,TypeError,InvalidOperation,OverflowError) as exc:
        raise AdmissionDenied('explicit reviewed expense alarm policy required') from exc


def threshold_crossed(spent_units, limit_units):
    if any(not isinstance(n,int) or isinstance(n,bool) or n<0 for n in (spent_units,limit_units)) or not limit_units:
        raise AdmissionDenied('fixed-point expense amounts required')
    return spent_units >= limit_units


def platform_daily_limit(policy, registered_user_count):
    """Count must be supplied by trusted operator-side account registry code."""
    if policy.overall_scope != 'platform_registered_users' or policy.day_mode != 'rolling_24h':
        raise AdmissionDenied('reviewed platform rolling-window policy required')
    if not isinstance(registered_user_count,int) or isinstance(registered_user_count,bool) or registered_user_count < 0:
        raise AdmissionDenied('verified registered-user count required')
    return policy.daily_per_registered_user_units * registered_user_count


def rolling_expense(rows, now, *, window_seconds=86400):
    """Pure settled-cost aggregation; uncertain usage never becomes zero.

    Rows must be trusted normalized ledger records, using settlement timestamps.
    Admission treatment of in-flight holds is a separate implementation decision.
    """
    if not isinstance(now,(int,float)) or isinstance(now,bool):raise AdmissionDenied('timestamp required')
    total=0
    for row in rows:
        if row.get('state') in ('dispatched','uncertain'):
            raise AdmissionDenied('unsettled provider usage requires explicit hold accounting')
        if row.get('state') != 'settled':continue
        timestamp=row.get('settled_at');expense=row.get('expense_actual')
        if not isinstance(timestamp,(int,float)) or isinstance(timestamp,bool) or not isinstance(expense,int) or isinstance(expense,bool) or expense<0:
            raise AdmissionDenied('verified settlement time and expense required')
        if now-window_seconds < timestamp <= now:total += expense
    return total
