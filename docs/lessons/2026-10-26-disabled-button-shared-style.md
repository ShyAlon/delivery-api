# 2026-10-26 - One shared disabled-button style (specificity is the contract)

## Problem
Shy: the disabled Start Interview button shows the not-allowed cursor and no
hover animation, but other disabled buttons still animate on hover. Cause:
`#startengine` had an ID-scoped `:disabled` patch (specificity 1,1,1) that
out-specified every class `:hover` rule, while all other buttons relied on the
shared `button:disabled` rule (0,1,1), which loses to `.primary:hover` (0,2,0)
and `.phase-actions button:hover` (0,2,1). Two more per-surface patches
(`.gate-page-actions button:disabled`, `body.ops-page #frame
button:disabled`) papered over the same losing battle.

## Fix
ONE shared rule carries the whole contract - rest, :hover and :active
selectors, so class-based hover rules cannot out-specify it:
`button:disabled,button:disabled:hover,button:disabled:active{opacity:.5;cursor:not-allowed;box-shadow:none;transform:none;filter:saturate(.6);transition:none}`.
Base rules that would still fight it are scoped `:not(:disabled)`
(`.phase-actions button:hover` color flip; the ops chrome drops its
ID-scoped `cursor:pointer`, already covered by `button{cursor:pointer}`).
The three per-button/per-page patches are deleted - no dead code.
`button.is-busy` keeps its `cursor:progress` with an explicit `:hover`
variant so the shared `:disabled:hover` rule does not out-specify it.

## Lessons
1. A "shared" CSS rule is only shared if it WINS. Check specificity against
   every class-based `:hover`/base rule that touches the same elements;
   element+pseudo selectors lose to class selectors.
2. Per-button patches are a symptom that the shared rule loses somewhere.
   Fix the shared rule (add state variants, scope competing base rules with
   `:not(:disabled)`), then delete the patches.
3. When a shared `:disabled:hover` variant is added, re-check deliberate
   state styles that must keep winning (here: `is-busy` progress cursor) -
   give them an explicit variant instead of relying on source order.
4. CSS contract tests that parse selector lists must strip block comments
   first - comment text with commas silently joins the first selector.
