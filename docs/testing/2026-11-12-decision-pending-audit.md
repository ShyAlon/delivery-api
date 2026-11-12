# Decision action feedback audit

## Cause and scope

The reported findings page already disabled its native controls and had a POST
re-entry guard. It was not evidence that the component migration removed the
request guard. Its radio inputs are invisible; the visible label/span pills had
no disabled styling. The only spinner/progress lived after tall full-width note
fields, below the viewport. The pending footer also switched to static layout.

Use one shared form pending lifecycle for findings, concept selection, final
interview approval/rework, gate approve/edit/rework, and gate semantic/technical
choice mutations. Preserve controls that were intrinsically disabled during
error recovery. Keep accepted decisions locked through handoff. The existing
shared runAction owns request re-entry; the form lock owns the longer
precondition/read/POST/handoff interval, without resubmitting an accepted answer.
Gate evidence refresh waits while committing so a refresh cannot replace locked
controls with enabled ones. Gate payload is captured before disabling fields,
because FormData omits disabled fields.

## Audited paths

| Path | Existing behavior | Change |
| --- | --- | --- |
| Findings Keep/Remove/Replace and Apply | Native disabled, invisible-input pills looked active, off-screen feedback | Shared form lock, disabled pill styling, responsive evidence/actions columns, smaller note fields, visible status |
| Concept choice and approval/ConOps rework | Manual lock, progress, accepted-read recovery | Shared form lock; preserve accepted recovery and input values |
| Gate approve/edit/rework | Shared request guard locked only action buttons | Lock whole review, visible text plus motion, keep committed state locked; pause evidence refresh |
| Gate findings and technical-choice save | Shared request guard locked only save button | Lock evidence editor while saving; preserve stale-review fence |
| Comment/draft/toggle | Shared request guard with retry and visible result | Unchanged request semantics; general disabled pill style applies |
| Task interview Send/Cancel | Explicit sending and awaiting-next guards, shadow state survives refresh, spinner/text, cancellation/retry, accepted form locked | Audited, not replaced: cancellation and server reconciliation are distinct from decision approval |
| Task Start/next-phase/retry/rerun, task create/rename/delete and account switch | Existing request/busy guards and confirmation/dialog lifecycle | Audited; do not alter execution, payment or confirmation semantics in a decision-layout repair |

This fixes the reported decision presentation and consolidates decision form
locking. It does not claim every server action in the product uses one helper.
The older infrastructure todo should not be closed solely on this PR.

## Evidence boundaries

Hermetic browser runs use real application assets and disposable PostgreSQL,
with held/error answer HTTP responses and no model or paid calls. Regression
covers desktop/mobile, form re-entry, intrinsic disabled choices, recoverable
errors, stale review, accepted-read failure and unchanged answer payloads.
Screenshots inspect real pixels, not only DOM state. Hosted CI is unavailable;
local gate only remains the owner's accepted process.
