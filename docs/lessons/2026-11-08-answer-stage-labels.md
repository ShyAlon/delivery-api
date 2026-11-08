# 2026-11-08 Answer stage labels must name observed signals, never invented precision

## What happened
During a real DeepSeek run a founder watched "Processing your answer..." for
505s with "last engine signal 380s ago" while the run progress strip still said
"Waiting for you", the answered question stayed on screen, and the Start view
kept showing "waiting for first persisted output" mid-answer. Every label was
technically wired; none of them described what was actually happening.

## Lessons
1. A UI stage label is a claim. It may only name a stage whose signal was
   observed (journal progress event, engine timing line, persisted flag). No
   signal, no label - fall back to the honest blanket state.
2. Signal age ("last engine signal Xs ago") is only meaningful while work is in
   flight. While the engine waits for the founder the age counts the founder's
   thinking time and reads as a stall. Gate it on in-flight work.
3. In-flight starts at submit, not at the POST response. The local shadow must
   be written before the await, and the server must persist its own
   answer_in_flight so a mid-flight re-render (new tab, refresh) shows the same
   truth. Local-only state lies on re-render.
4. The engine already emits structured timing lines (#689). Retaining them in a
   debug ZIP is not enough - map the honest subset (staging.ready,
   saver.setup.begin/end, advance.turn.begin/end, session.saved) to task
   progress events at the supervisor boundary so the UI refines stages live.
   Unmapped records stay invisible; do not widen silently.
5. The waiting question, the run-progress scope, and the phase-output slot are
   three render paths that must agree on the same answerPending boolean, or the
   page contradicts itself within one screen.
