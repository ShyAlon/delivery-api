# Use the same guard at every interactive server-action boundary

The shared runAction helper existed, but purchase receipts, gate annotations,
feedback and other secondary actions bypassed it. Some surfaces had local
flags; others only disabled a button, which did not stop a programmatic second
submission. Consolidate lifecycle handling while preserving domain checks:
review revisions, stale rejection, accepted decisions and unresolved payments.

Inventory: purchase pay/status/receipt; gate replay/proposal/semantic/stack/
comment/draft/toggle/decide; typed decision annotations and apply; QA feedback;
operator preview/grant/refresh/log download; completion packaging; requirement
and founder dialogs. Copy-blocks is local clipboard/download work with its own
guard, and gate-ui/ops-table are local controls, not server-call bypasses.

Use resource-scoped keys for independent tasks/feedback. Competing actions on
one gate or one grant still share a key. UI freshness refresh must not re-enable
a button with aria-busy. Keep stale/committed state after lifecycle cleanup.
Do not flatten nested button content on cleanup unless a busyLabel replaced it.

Failing first: real receipt callback and gate-comment duplicate/retry tests
failed on old code. Real approval test holds a request, blocks duplicates,
permits retry after 500 and preserves a stale 409 block. Payment outcome and
receipt verification assertions remain intact. No money/network effects in
tests: the purchase boundary is a local fixture.

Existing baseline failures, reproduced on unchanged main 24ca601:
- test_apply_shows_busy_then_success_feedback (progress wording)
- test_gate_transition_reconciles_delayed_next_phase
- test_phase_change_dialog_closes_when_the_founder_can_act_again_and_never_shows_for_a_question
The latter two still assume a short refresh after the approved 60-second
safety cadence. These are assigned separately; no assertion is removed here.
