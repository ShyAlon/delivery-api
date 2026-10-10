# Payment preparation boundary

Payment processing is not enabled. Provider account, merchant jurisdiction,
processor choice, tax treatment, refund/dispute rules and purchase pricing need
source-grounded decisions before a checkout adapter is selected or published.

The first independent implementation is provider-neutral evidence handling:
- Freeze server-selected offer, currency, amount, owner and credit scale.
- Only a trusted processor verification adapter may produce fulfillment evidence.
- Browser success redirects, customer JSON, model output and emails cannot grant
  credits or supply an authoritative price, owner or paid status.
- One confirmed payment may credit one immutable purchase exactly once.
- Duplicate callbacks are no-ops only if payment identity and payload agree.
- Conflicting callbacks, refund/dispute notices and unverified/missing state stay
  in operator review. Do not invent a refund policy or automatically debit users.

No secret, provider account, production migration, public payment route or charge
is introduced by preparation-only code. Public execution remains fenced.
