# One dialog lifecycle across both surfaces

Before: customer dialogs used an overlay wrapper plus direct markup writes;
Back office had native constructors in pins, audit and maintenance. Native
focus handling did not cover customer overlays, and Add model had aria labels
without visible labels. Counts of wrapper calls are not counts of user journeys.

Inventory: customer New task, Rename, Delete, route switch, Replay, failed-needs
retry, failed-phase retry, next phase, rerun, task audit, phase transition, Stop,
Stop status, run Stop, artifact viewer, feedback, Debug, missing resource,
Greenfield correction, founder decision and action error (21 kinds). Private
Add model, Edit pin, Review pin, Expense policy, maintenance confirmation,
Audit evidence and Audit resolution confirmation (7 kinds). Gate reviews are
pages, not dialogs; their default Approve and payloads are unchanged.

All 28 kinds use shared-dialog.js lifecycle. Native dialogs are created only
there. Customer overlay descendants are enrolled centrally when mounted, so
legacy and nested views cannot skip focus trapping/accessible naming/Esc/Enter.
Existing action-specific form submissions and multiline semantics are retained.
Add model/price/limit reviews and maintenance use the shared action/status/field
builder, never duplicate busy/cancel/error construction. New dialogs use it.

Do not infer a successful mutation from dialog close or a green button. Keep
current API result verification, owner/proof guards and explicit confirmations.
