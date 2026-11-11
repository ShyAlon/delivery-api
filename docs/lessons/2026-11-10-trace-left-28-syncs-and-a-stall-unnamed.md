# 2026-11-10: the trace left 28 syncs and the last stall unnamed

## What happened
The first debug ZIP with the budget lines showed 28 syncs (12 transactions, 3.8 s) under `unattributed[thread=AnyIO worker thread]`, 80 of 145 purposes logged, and one 437 ms loop stall whose log field showed only psycopg and contextlib frames.

## Fix (logging only)
- Unattributed entries name the nearest non-plumbing frames: `unattributed[thread=...;from=module:function<...]`.
- All purposes of a request are logged (cap 250).
- The stall log keeps the 4 innermost frames and adds `asked by:` with the nearest 6 delivery/engine frames, within the 500-character field cap.

## Rule
A diagnostic that cannot name the caller sends the next round trip of guessing back to the owner. Name the caller.
