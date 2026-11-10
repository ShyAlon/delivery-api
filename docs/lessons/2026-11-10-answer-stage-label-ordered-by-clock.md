# 2026-11-10: the answer stage label compared two clocks

## What happened
"Processing your answer…" could stay generic for the whole answer even though the engine was emitting stage events.

## Cause
The browser decided which progress events belonged to the answer by comparing its own `Date.now()` (at submit)
with the server's whole-second event stamps. If the server clock (Docker VM) runs more than ~1.5 s behind the
browser, every event looks older than the submit and none is shown.

## Fix
Order by event id, which the server assigns in sequence. The submit shadow stores the newest event id the tab had
(`after_id`); the durable `answer_submitted` event's id is the floor when there is no shadow. Clock comparison stays only
for an older stored shadow without an id.

## Still clock-dependent (display only, not changed here)
"N s on this step" and "last update N s ago" subtract server stamps from the tab clock; under skew the numbers are off.

## Rule
Order events by the server's sequence. Never decide what belongs to a turn by comparing a tab clock with a server clock.
