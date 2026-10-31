# An answer callback must own its render

The Meditator7.97 diagnostic export confirms an old request callback overwrote a newer task render. The saved answer event binds to the correct submitted input. Do not repair stored transcript data to fix this display race.

## Evidence

Task c8ce3192108b,30October2026 (UTC browser timestamps):

-20:16:12.068: answer submit begins.
-20:17:25.547: task rerenders executing while POST remains pending.
-20:18:36.528: task snapshot renders the next engine question; feedback finishes37.637.
-20:18:46.383: original POST returns200 after154315ms.
-20:18:46.384: original callback updates the shadow using the question from the current DOM, now the next question.

Event356 stores that answer under input1ca3b37e-b182-41c0-9672-a9fa818f65fe and its original session-flow question. Current input53c5d8aa-75fd-4e3e-be5c-bacb64306719 asks about content origin. The saved pairing is correct. The task render also predates event356, explaining the one-exchange history in the screenshot. Raw debug export is not committed.

## Cause and fix

The submit closure waited for POST, then queried global #currentquestion and #shadowcopy. SSE can replace those nodes before POST completes. Navigation epoch alone does not change on an in-place task redraw.

Capture submitted question and input identity before awaiting. Add a task-render revision, and discard success/failure DOM effects from a callback whose render was replaced or whose page exited. Do not mutate or disable the new composer, silently replay a request, refresh over its typed draft, or pair a current prompt with an old answer. Identity/revision-only client diagnostics distinguish this race without recording answer text.

## Reproduction and validation

New Playwright regression uses the real task page submit and SSE callback, holds Q1 POST, renders Q2, then releases Q1 response. It fails before the fix because the shadow is visible, and passes after. It checks outgoing Q1 preconditions, visible Q2, enabled composer and reload behavior. Existing shadow success/failure and answer/history tests remain unchanged.20focused browser tests passed; no tests retired. A screenshot was inspected: Q2 appears above an empty enabled composer, without a processing shadow.

No live writes, paid calls, deploy or installed-runtime verification. Test engine responses are controlled; this is not a paid-engine end-to-end run. The existing same-input history-refresh behavior is not redesigned here.
