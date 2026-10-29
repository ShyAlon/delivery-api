# Browser route callbacks cannot run while Python blocks its event loop

The stale dashboard mount test waits for a Playwright route callback to append
a request to a list. time.sleep() can starve those callbacks: isolated unchanged
case failed on both profiles at the eight-second guard, while a previous
module run passed current. A false race outcome, not evidence the dashboard
never made a request.

Pass page explicitly to _wait_for and use page.wait_for_timeout(50) rather than
Python time.sleep(.05), pumping Playwright events. Keep exact wait deadlines,
held-request counts, navigation, release order, 11s polling check and every
assertion unchanged. No unconditional pass, added retries or timeout increase.
Three stale-mount cases pass both profiles; measured times in PR. Dated evidence
from full audit. Test-only; no production changes. Hosted CI/full suite unverified.
