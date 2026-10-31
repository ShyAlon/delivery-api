"""Explicit owner-scoped alert delivery seam; no runtime, SMTP or scheduler install.

A trusted caller supplies its separately verified fixed route/transport. Nothing
from an outbox payload chooses a recipient, sender or transport. Claim commits
before send; missing/unknown acknowledgment is held for review, never replayed.
"""
from dataclasses import dataclass
from typing import Callable,Literal
from .attempt_ledger import AdmissionDenied

Outcome=Literal['accepted','transient_failure','permanent_failure','unknown']


@dataclass(frozen=True)
class ExpenseAlertTransport:
    # A route binding identifies the operator-reviewed destination without putting
    # address/account credentials in the outbox or letting payloads select them.
    route_binding: str
    send: Callable[[str,dict],Outcome]

    def __post_init__(self):
        if not isinstance(self.route_binding,str) or not self.route_binding.strip() or not callable(self.send):
            raise ValueError('fixed alert route and transport required')


def deliver_alert(store,alert_key,transport):
    if not isinstance(transport,ExpenseAlertTransport):raise TypeError('typed fixed alert transport required')
    # A request transaction must not delay the durable claim until after send.
    from .postgres_scope import _request_tx
    if store._connection.get() is not None or _request_tx.get() is not None:raise AdmissionDenied('alert delivery requires its own commit boundary')
    row=store.claim_expense_alert(alert_key)
    if row is None:return None
    try:
        # Only expense evidence, not arbitrary strings/recipients/private task data.
        from .expense_alert import prepare_alert
        p=row['payload']
        evidence=prepare_alert(**{key:p[key] for key in ('episode_id','scope','scope_id','spent_units','limit_units','units_per_usd','occurred_at')}).payload
        outcome=transport.send(alert_key,evidence)
        if outcome not in ('accepted','transient_failure','permanent_failure','unknown'):outcome='unknown'
    except Exception:
        outcome='unknown'  # Includes timeout after possible acceptance. Do not retry blind.
    return store.finish_expense_alert(alert_key,outcome)
