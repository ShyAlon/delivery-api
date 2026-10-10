"""Provider-neutral payment boundary errors."""
class PaymentUncertain(RuntimeError):
    """Keep intent and reconcile; never retry under a new payment identity."""
class PaymentRejected(ValueError):pass
