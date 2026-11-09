# 2026-11-09 Durable anchors outlive tab-local state

## What happened
A founder answered a question, re-rendered the page mid-flight (fresh page
load 27s after submit), and watched "Processing your answer..." sit unchanged
for the whole turn while the engine was healthy and progress signals were
streaming. The refinement gate (answerProcessingLabel) orders signals against
a submit timestamp kept only in the submitting tab's sessionStorage shadow.
The server persisted answer_in_flight as a boolean - so the pending STATE
survived the re-render - but not the anchor TIME, so the gate could never
open again in the new context: every healthy signal was discarded, by design
("never a guess"). Half of 2026-11-08's own rule had shipped: state yes,
anchor no.

## Lessons
1. Client-only anchors make server-rendered states lie. When the server owns
   a state (answer in flight), the server must also own the anchor that
   scopes it. The submit event (answer_submitted) is written before the
   blocking engine advance, so any re-render can order post-submit signals.
2. Kind namespaces are contracts. The 'answer' event already meant
   turn-end-and-render-QA (qaTurns pairs it with the question); an early
   'answer' row would have rendered a phantom exchange and then doubled it.
   A new anchor gets a new kind, never a reused one.
3. Source reading beats emission-site assumption: session.saved LOOKED
   child-dark (its only emit site is http_service.py) but the child runs the
   service's SessionRegistry in-process - the post-fix export showed
   engine.persist 'Session saved' events with outcome=published on every
   turn. Check what the container actually emits before declaring a map
   entry dead.
4. Exact-sequence test assertions on event kinds are change detectors, not
   bugs: when a new event is the fix, update the sequence and say why in
   the assertion message.
