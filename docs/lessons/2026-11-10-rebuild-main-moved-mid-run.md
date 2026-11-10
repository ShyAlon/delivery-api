# 2026-11-10: rebuild failed at preview because origin/main moved during the run

## Problem
Twice (2026-11-09 19:06 and 2026-11-10 17:18) `req rebuild --apply` reached
`req preview --apply` and failed with "preview requires exact current local
acceptance; run --accept-local first". Both times the failing child's own output
began with a git fast-forward of delivery-api (4308f08..21f8b1c, then #724).

## Root cause
Rebuild pins the six origin/main revisions and the runtime/acceptance contracts
once, at start. Every child `req` command fetches and fast-forwards main, and
image_workflow refuses a checkout behind origin/main. A merge during the run (a
build, or a long typed prompt: 5428 s the second time) therefore changes the
sources and contract the preview child computes, so the image accepted for the
older revisions is no longer "exact current". The orchestrator never looked
again after step 1, so it walked into the child and failed there. Not a hash
over files that change after step 4, and not a skipped accept step.

## Fix
- `ensure_current` fetches all six repos and compares origin/main with the pinned
  revisions before accept-local, migrate and the preview restart. If anything
  moved it names repo and revisions, then restarts through the req front-end (new
  code, new contracts, new key); recorded steps with unchanged inputs stay reused.
  Capped at 3 restarts, then fails naming what moved.
- `heads` now reads each checkout's HEAD (the pull-all verification test pinned
  this and was failing on main since the orchestrator landed; tests ran with
  `[skip ci]`).

## Residual
A merge in the seconds between the last check and the preview child can still
race; the preview then fails with the same message. Rerun picks up the new main.
