"""Exact operator policy USD conversion. No customer approval flow."""
from decimal import Decimal, InvalidOperation
import time
from .attempt_ledger import AdmissionDenied
from .tenancy import current_scope
SCALE=10**12


def limit_units(value):
    try:
        amount=Decimal(value)
        units=amount*SCALE
        if not amount.is_finite() or amount<=0 or units!=units.to_integral_value() or units>2**63-1:raise ValueError()
        return int(units)
    except (ValueError,TypeError,InvalidOperation):raise ValueError('Enter a positive USD ceiling with at most 12 decimal places') from None
