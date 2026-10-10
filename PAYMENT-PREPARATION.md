# Private payment implementation and rollout boundary

This branch implements PayPal Orders v2 behind an injected server transport and
provider-neutral purchase contract, payment lifecycle and PostgreSQL ledger.
It does not activate payments. No environment credentials, OAuth client factory,
account access, vault read, live/sandbox PayPal call, mounted route or real charge.

Q01 offers are exactly owner-approved October 10, 2026 at 08:06:07 Jerusalem:
5 credits/$5, 12/$11.50, 25/$23, 50/$45. Resent workbook SHA256:
14b6a75930f26ab04a34d8ab2dba3c12fcf352a95fc78f21f13eaf8648d03eb8,
answer!C5; later authenticated "As proposed" confirmation. Scale is supplied
from the provisioned account, not guessed or selected by the customer.

## Payment rules

- Freeze owner, offer/version, currency, exact integer amount and credit scale.
- Browser success/approval/PENDING cannot grant credit. Only a matching completed
  full capture, read server-side and merchant/order/amount-bound, can do so.
- Persist distinct create/capture request IDs before external work. Timeout does
  not rotate a key or order. PayPal adapter uses a five-hour conservative replay
  window within documented six-hour retention; expired uncertain work stops for
  reconciliation. Durable local payment uniqueness outlives the provider window.
- One capture and one purchase credit posting in a transaction with account
  balance increment. Receipt+effect rollback together. Refund/reversal/dispute
  before or after paid completion parks for review, without automatic debit or
  re-grant. A paid callback cannot reopen review.
- Verify exact notification body and headers against the configured webhook ID.
  Signed events are still data, not authorization. Parent capture URLs are only
  parsed under strict fixed-origin/path rules; arbitrary event URLs are not fetched.
- Separate restricted payment-ingestion login; no ordinary browser/backend role
  can journal or credit a purchase. No role memberships are provisioned in the
  migration. Runtime rejects superuser/bypass/tenant-backend/schema-owner logins.
- Router factory is unmounted and disabled by default (404). Local private tests
  inject active-session owner resolver and stubs only. A local purchase ID is
  returned before remote checkout, avoiding a lost response causing new charges.

## Cost visibility and processor replacement

Actual gross/processor-fee/net capture evidence is recorded separately from
provider expense. Missing receiving costs remain unknown, never zero. Neutral
reports group by processor/merchant/mode, owner, offer/version and currency in an
explicit period. This is receiving-cost reporting, not profit.

Existing back office does not expose payment reporting yet. Exact profit still
needs withdrawal/FX/tax, refunds/dispute losses/fees, operations costs, and
provider-cost allocation to credit lots. Session expense tripwires do not prove
pack margin or bound a user's total provider expense to one session.

PayPal API paths/status/events/fees and replay-window policy stay in its adapter;
ledger/reporting/credit contract are processor-neutral. A future provider needs
an adapter implementation and reviewed eligibility/terms, not a balance rewrite.

## Rollout work still fenced

Merchant identity/commercial readiness, USD/card eligibility, acceptable-use
classification, real signature/schema/account evidence, credentials, tax and
refund/dispute policy need verification before activation. IL Seller Protection
excludes digital goods/licences and cash-equivalent vouchers; no protected
classification is assumed. FX, holds and fees remain real costs.

No public payment route, production migration, user UI or back-office read grant
is activated. Full clean-run evidence remains the owner's step-6 launch boundary.

The private reconciliation candidate queue is bounded and inactive. A process
must reconcile pending/lost notifications and expired remote-key cases before
real activation; no live scheduler, retry worker or owner notification is wired.
The API factory requires a caller-supplied mutation/CSRF guard for authenticated
POSTs, independently of owner/session validation. Webhooks require signature
verification, not user cookies. Application middleware/wiring remains unmounted.
