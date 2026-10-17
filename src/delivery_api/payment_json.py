"""Strict parsing of webhook event bodies: bounded size, exact bytes, no duplicate keys, no NaN or Infinity, top level must be an object. Any violation raises PaymentRejected."""
import json
from .payment_errors import PaymentRejected

def strict_json(raw):
    if not isinstance(raw,bytes) or len(raw)>262144:
        raise PaymentRejected('bounded exact webhook bytes required')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise PaymentRejected('duplicate JSON key')
            out[k]=v
        return out
    try:
        event=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(PaymentRejected('invalid JSON number')))
    except (ValueError,TypeError,UnicodeError) as exc:raise PaymentRejected('invalid event JSON') from exc
    if not isinstance(event,dict):raise PaymentRejected('event object required')
    return event


