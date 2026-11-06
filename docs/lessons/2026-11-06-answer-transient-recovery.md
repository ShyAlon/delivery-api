# 2026-11-06 - A dropped connection mid-answer must recover without a refresh

## What happened
Owner clicked Send answer, the network dropped for about 2 minutes. The child
died on a database OperationalError, the progress watcher died uncaught, the
POST /answer failed after 162s, and the owner's own retry hit a fresh child that
met the still-degraded pool and then a 409 "stale founder decision". Typed text
was lost on refresh. No money was at risk (0 pending attempts).

## Cause
Transient database loss was treated as a terminal failure in three places:
the watcher caught only its own error types, the submit path started a NEW job
on retry instead of resuming the paused one (same operation id), and the
duplicate-accept check turned the user's own retry of the same answer into a 409.
The page showed a dead end with no Retry button.

## Rule
- Every background loop that touches the database survives transient
  OperationalError and keeps running.
- A retry of the same answer resumes the paused durable job (bounded attempts,
  waits out a live prior lease); it never spawns a second operation.
- The user's own retry of an already-accepted answer is a success, not a conflict.
- A failed send keeps the typed text and offers a working Retry button.
- Fail-first tests drive the real journey: drop the DB connections mid-answer,
  assert the job pauses and the same operation resumes.
