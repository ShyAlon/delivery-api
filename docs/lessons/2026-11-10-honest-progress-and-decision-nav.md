# 2026-11-10: the progress box lied and the decision page fought the user

Problem (screenshots 17:31 and 17:34): "Progress connection lost ... 207491s elapsed"
while the page only waited for the user; the page sat on "Waiting for your decision"
instead of opening Review Flagged Inputs; jargon in the shared progress block;
a 200 px dead gap above the buttons; a banner repeating the applying state;
a 150 px sticky header covering the textarea.

Causes and fixes:
- Initial render hard-coded connected:false. Now tri-state (Connecting / Live / paused,
  retrying). Nothing claims "lost" before the stream has had a chance to answer.
- Elapsed used the run start (07:52 Nov 8). Now it counts from the answer being
  processed (tab stamp or durable answer_submitted event), and a waiting-for-you
  state claims no elapsed time at all.
- No navigation on a decision. Now the transition into a decision or gate opens its
  page, never on first load and never over text the user is typing.
- #semanticstatus used flex:1 1 200px; in the stacked footer the basis became a
  200 px height. Now flex:1 1 auto.
- The decision checklist repeated the applying state next to the progress block;
  it is hidden while applying (its blocker still gates the button).
- The sticky header is static on viewports under 900 px tall.

Rule: a flex-basis meant as width is a height in a column. Tri-state a connection
flag: unknown is not lost.
