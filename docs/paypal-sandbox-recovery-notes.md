# PayPal sandbox and webhook recovery notes

Updated: 2026-10-14. Scope: the isolated item6 sandbox run, not live checkout.

## Current recovery state

- Order `3E416743FF2098038` has completed final capture `0NC89963FU721743D` for USD 5.00 in sandbox.
- Purchase: `item6-topup-4eda39c0684945ff849b582c60dd91f8-02`, a 5-credit pack.
- The capture completed at `2026-10-13T17:01:27Z`. Its invoice/custom IDs match the frozen purchase intent.
- The local credit posting, never-dispatched attempt release and failed-compute waiver completed in `items-topup-continuation-04`.
- Authoritative successor dump: `ef4a209905d0f294484740d1149bde2038621110f3269770719e9e17190ab889`.
- Continuation has NOT started. It is blocked on receipt and signature verification of the authentic capture webhook.
- PayPal created event `WH-9B13847097755005V-31R4816806676591W` at `2026-10-13T17:01:30.910Z`. The receiver had no matching receipt at the latest check.
- Subscription `3V6772435R887650W` points to `https://15.235.101.85/sandbox/paypal/webhook`, with `PAYMENT.CAPTURE.COMPLETED` enabled.
- The latest checks found services active, TLS valid and receiver health OK. They do not establish why this event was not stored. Pending/delayed delivery and rejected ingress remain possible. Do not label this a confirmed PayPal-side defect.

## Distinguish approval, capture, posting and webhook evidence

These are separate steps, not interchangeable success signals:

1. Order creation returns a processor-bound approval URL. It does not move money.
2. Buyer approval changes remote order state. A browser redirect or unchanged page alone does not prove it.
3. Capture can complete remotely while the local process fails before fulfillment.
4. Local fulfillment posts credits atomically against validated processor evidence. It must not create a second payment.
5. Authentic raw webhook bytes plus original transmission headers must be verified against the configured subscription before the continuation evidence gate passes.

The last successful payment leg must never run again simply because step 5 is missing. A GET response containing an event body is not the original delivery envelope. Do not invent transmission headers, simulate a capture event or substitute a locally serialized JSON body for the raw signed bytes.

## Failures observed and fixes

### Disposable database-name guard

The first combined launcher supplied `requi_item6_items`. The bootstrap requires a local host and a database name matching `requi_item6_[a-z0-9_]{8,60}`. The suffix `items` was too short.

This failed before archive restore, payment repository construction, OAuth or order creation. The generic "payment may have completed" banner was conservative and misleading for that early frame. No order was created by this failed invocation.

Fix: use a valid run-specific name such as `requi_item6_items_4eda39c06849`. Test the actual bootstrap guard, not just Python syntax. Test that a bootstrap exception exits before restore/network/payment effects.

### Capture GET blocked by the network boundary

A later launcher allowed `/v2/payments/captures/<cid>` only when `<cid>` matched the local purchase row's `capture_id`. That field was still null: fulfillment had not happened yet.

The capture POST succeeded remotely. `completed_evidence` read a COMPLETED order, discovered its completed final capture, then its required capture GET was blocked by our own boundary. The local ledger stayed unfulfilled.

Fix: discover and validate the capture from the exact immutable order, or bind the already-confirmed capture ID explicitly. A confirmed-capture recovery should permit only exact order/capture GETs plus OAuth/signature verification. It must forbid order-create and capture POSTs. Test the actual network transport with null local capture ID, allowed exact GET, wrong-ID rejection and payment-POST rejection.

### Interactive prompt hidden by output redirection

Running `bash script.txt > output.txt` hid the sandbox buyer approval prompt. The process was waiting for `SANDBOX APPROVED`, not stuck. Stopping it preserved an existing CREATED order. Its output-directory guard correctly refused a blind rerun.

Run attended scripts without redirecting output. Open the processor-returned existing approval URL, approve in the sandbox buyer account, then type `SANDBOX APPROVED` in the terminal. Re-GET the order before any capture. Use the actual downloaded attachment filename, including any suffix.

### Capture event absent from receiver

The bounded receiver watch found no matching event. A later check confirmed the event exists at PayPal, but the receiver remains empty for this capture. The API response did not expose delivery status. Logs were intentionally disabled for HTTP request history, so no error category is not proof of successful ingress.

Next scoped recovery: one resend of the exact existing event to the exact existing subscription, then export and verify the arriving raw envelope. HTTP 202 is request acceptance, not delivery proof. PayPal documents that pending notifications are not resent. If no receipt arrives after the bounded watch, inspect this exact event's delivery status in the sandbox app's Webhooks Events dashboard before another effect.

## Recovery rules

- Keep every output directory and successor dump on success, interruption or failure. Never delete the directory to defeat the guard.
- Recover from the latest ledger containing the existing purchase, not the older pre-order archive.
- Keep the frozen owner, merchant, sandbox mode, currency, pack, intent digest, order ID and request IDs together.
- This purchase's create request ID is `3cfb7f4a-63c6-5b83-a10e-9b906982ce4c`; capture request ID is `f1f2eeb3-7959-5a78-ab58-d157045278bc`.
- Check current processor state before deciding the next action. CREATED needs buyer approval; APPROVED may be captured within the approved scope; COMPLETED needs read-only reconciliation; expired, unavailable or conflicting state needs a decision, not a replacement order.
- A timeout is an unknown outcome, not permission to repeat a POST. Keep the original request IDs and inspect state first.
- Once capture is confirmed, remove capture/create POST capability from the recovery path entirely. Locally fulfill the same purchase using validated completed evidence.
- Local credit posting is idempotent against the purchase/payment key. Verify one payment-credit row and the expected balance after posting.
- Never-dispatched reserved attempts can be cancelled after verifying no provider start, usage, wire evidence, token contribution or charge. This is not an unknown-provider-outcome waiver.
- Unmeasured failed-job compute requires a separate explicit write-off approval and an audit record. Keep measurement unknown; do not fabricate a measured zero.
- No new pack, cap reset, automatic paid rerun or live-payment activation is implied by recovery.
- Product content gates stay attended. Successful linked recovery is `CONTINUATION_EVIDENCE_READY`; the original interrupted acceptance stays `INCOMPLETE`.

## Provider binding and continuation

The saved approved architecture is complete; continuation should start at `items`, not replay architecture. Inspect checkpoint.next before any paid work.

The provider snapshot is a separate validity window from PayPal evidence. Its old binding expires `2026-10-14 09:32:39 Asia/Jerusalem`. Refresh by fetching and parsing current vendor sources, recording their real hashes/time and producing a new private runtime review. Do not update only a timestamp or replay an old approved review hash. Preserve retail formula, owner cap, source/checkpoint and settled historical rate evidence. A refresh proposal is not paid continuation approval.

## Verification checklist before shipping another launcher

- Bash syntax and embedded Python compile/import checks.
- Actual bootstrap guard regression, including invalid old name.
- Native archive hash and private input-tree checks.
- Restored owner/purchase/request-ID/ledger-state checks before effects.
- Exact-order/capture transport allowlist tests, including null local capture ID and forbidden payment POSTs.
- Correct output-directory and successor-dump paths in every leg.
- Dump export on failure after restoration.
- Exact raw webhook and transmission-header signature verification.
- No payment/release/waiver replay when only webhook evidence or continuation remains.
- Honest reporting of tests mocked locally vs native checks that still need the owner's Mac. Compile success is not end-to-end proof.

## Official references

- Event listing: https://developer.paypal.com/api/webhooks/v1/webhooks-events-list
- Webhooks API: https://developer.paypal.com/api/webhooks/v1
- Exact-event resend, including pending-notification caveat: https://developer.paypal.com/api/webhooks/v1/webhooks-events-resend
- Dashboard delivery status: https://developer.paypal.com/api/rest/webhooks/events-dashboard

These notes record observed behavior and known launcher defects. They are not authorization to spend, send a webhook, waive accounting or start another run.
