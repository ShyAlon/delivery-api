# Customer credit purchase UI

The /credits page implements the reviewed choose/review/payment-state design.
The deployed boundary is closed: it makes no payment calls, returns no invented
account balance, and disables PayPal checkout with a readable cause/next step.
Payment routes remain unmounted and the launch fence remains false.

The browser fixtures inject a controlled adapter into createPurchase only in
local test HTML. No fixture hook or URL-driven success appears in the workspace.
Production app.js creates the page with the closed default adapter. Query strings
preserve selected offer/view/purchase reference, never prove success or a balance.
Payment status and receipt evidence must match the purchase, currency, amount,
credits and posting confirmation. Unknown create outcomes remain blocked across
back/refresh in that tab; they do not cause another create.

Credit offer JSON is compared in tests against payment_offers.py and the reviewed
version. Customers see only credits and USD. The 12/25/50-credit packs display
4/8/10% discount badges. Provider rates, costs, tokens and raw payloads stay out.

Tests: tests/test_purchase_ui.py and tests/test_purchase_browser.py. Browser tests
use actual shipped modules, controlled fixtures, native receipt downloads,
320/390/1280px states, 320/390/1249px authenticated workspace shell, matching-status
rejection, unknown-outcome prevention and the closed payment boundary. Screenshot
inspection is required as well as assertions. No live PayPal/provider test is
included or implied. Live integration remains a separate reviewed rollout.
