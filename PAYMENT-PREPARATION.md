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

Pure lifecycle plans also cover delayed completion, repeated paid callbacks,
multiple distinct captures for one purchase, and refund/reversal/dispute events
before or after completion. Adverse events park the purchase for review, with
no automatic balance debit or re-grant. The lifecycle object is not a ledger:
separate persistence must enforce global unique payment identity and one
purchase credit posting, in the same transaction as the owner's balance write.

PayPal research found no country-wide IL merchant blocker but did find digital
Seller Protection exclusions, account-specific card eligibility, FX and holds.
That does not enable an account, select commercial terms or lift rollout fences.
