"""Transport-neutral expense alert preparation. No email side effects here.

Persist the returned key/payload in a backend-only outbox before a separate
verified sender attempts delivery. Callers must supply a durable breach episode
ID so a retry/rejected call cannot generate another alert for the same episode.
"""
import hashlib
import json
from dataclasses import dataclass
from .attempt_ledger import AdmissionDenied


@dataclass(frozen=True)
class ExpenseAlert:
    key: str
    payload: dict


def prepare_alert(*, episode_id, scope, scope_id, spent_units, limit_units, units_per_usd, occurred_at):
    if scope not in ('session','platform_rolling_24h'):
        raise AdmissionDenied('reviewed expense alert scope required')
    if not all(isinstance(v,str) and v for v in (episode_id,scope_id)):
        raise AdmissionDenied('durable expense episode identity required')
    if any(not isinstance(v,int) or isinstance(v,bool) or v<0 for v in (spent_units,limit_units,units_per_usd)) or not units_per_usd:
        raise AdmissionDenied('fixed-point expense alert values required')
    if spent_units<limit_units:raise AdmissionDenied('expense threshold not reached')
    if not isinstance(occurred_at,(int,float)) or isinstance(occurred_at,bool):raise AdmissionDenied('expense event time required')
    identity={'episode_id':episode_id,'scope':scope,'scope_id':scope_id}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    payload={**identity,'spent_units':spent_units,'limit_units':limit_units,'units_per_usd':units_per_usd,
             'occurred_at':occurred_at,'action':'new_calls_stopped','running_calls':'finish'}
    return ExpenseAlert(key,payload)


def delivery_outcome(*, outcome, attempts, max_attempts):
    """Status for a durable outbox, never a trigger to release admission stops."""
    if outcome not in ('accepted','transient_failure','permanent_failure','unknown'):
        raise AdmissionDenied('explicit delivery outcome required')
    if any(not isinstance(v,int) or isinstance(v,bool) or v<1 for v in (attempts,max_attempts)):
        raise AdmissionDenied('bounded alert delivery attempts required')
    if outcome=='accepted':return 'sent'
    if outcome=='transient_failure' and attempts<max_attempts:return 'retry_pending'
    # Unknown SMTP acceptance cannot be blindly retried, it may duplicate mail.
    return 'operator_review'
