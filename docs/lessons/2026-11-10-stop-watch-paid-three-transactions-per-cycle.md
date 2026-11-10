# 2026-11-10: the stop watcher paid three transactions per cycle

## What happened
The debug ZIP showed `stop_watch:watch_for_stop` with 6 COMMITs and 1.95 s in identity batches in one 79 s answer. It looked like 6 cycles against a 30 s setting.

## Cause
The cadence was right (2 cycles). Each cycle read the task, the session binding and the sign-in as three separate transactions, each with its own identity batch and commit: 3 x 2 = 6.

## Fix
`PostgresStore.stop_state(task_id, session_id)` answers the same checks (task present, stop requested, session generation, sign-in valid) in one statement. Nothing is cached; every cycle still reads the database. Stop click still wakes the watcher at once, revocation is still read every 30 s.

## Rule
A check that needs several facts reads them in one snapshot. The checks themselves are unchanged and every stop reason still stops the child (tested).
