"""Availability evidence, not an atomic provider hold or exchange-rate guess."""
from datetime import datetime,timezone
from decimal import Decimal
from .attempt_ledger import AdmissionDenied
from .provider_snapshot import timestamp

def validate_balance(evidence,reserved,scale):
    try:
        if not isinstance(evidence,dict) or set(evidence)!={'response','observed_at'}:raise ValueError()
        response=evidence['response'];observed=timestamp(evidence['observed_at'])
        age=(datetime.now(timezone.utc)-observed).total_seconds()
        if not 0<=age<=30 or response['is_available'] is not True:raise ValueError()
        rows=response['balance_infos']
        if not isinstance(rows,list) or not rows:raise ValueError()
        usd=[r for r in rows if r.get('currency')=='USD']
        if len(usd)>1:raise ValueError()
        if usd:
            total=Decimal(usd[0]['total_balance'])
            if not total.is_finite() or total<Decimal(reserved)/scale:raise ValueError()
            check='USD total at least estimated reservation'
        else:
            # Endpoint explicitly affirms availability. Do not invent CNY/USD FX.
            if any(r.get('currency')!='CNY' or not Decimal(r['total_balance']).is_finite() or Decimal(r['total_balance'])<0 for r in rows):raise ValueError()
            check='provider is_available only; no USD total/FX proof'
        return {'response':response,'observed_at':observed.isoformat(),'check':check,'atomic_hold':False}
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:raise AdmissionDenied('provider balance unavailable, stale or insufficient; new call held') from exc
