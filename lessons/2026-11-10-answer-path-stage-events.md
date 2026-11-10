# The answer's bare "Processing your answer" label had no real signal to show

Date: 2026-11-10

Problem: the UI already shows the newest progress event after the answer was submitted, but the engine commits ~1 progress
page per 30 s during an 85 s answer, so the label stayed blanket. In Shy's 07:51 answer the true boundaries were: received +0 s,
engine call starts +2.5 s, engine progress pages at +27 s, +61 s, +65 s, engine call returns +78 s, answer recorded +85 s.

Fix: two real progress events from the answer path: "Interview engine is working on your answer" (right before the engine
call that blocks) and "Recording your answer and preparing the next question" (after it returns). No timers, no percentages;
engine-side signals still replace them when they arrive. Each event is one statement (see add_event).

Not fixed here: a progress stream cycle blocked the event loop for 59 s (loop_blocked_ms 84,387 in one stream), so a stage event
can still reach the browser late. That needs its own PR.
