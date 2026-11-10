# 2026-11-10: the owner was asked to type "APPLY 0 MIGRATIONS TO delivery_app"

## What happened
`req rebuild` asked for "APPLY MIGRATIONS", then `req migrate --apply` asked for "APPLY 0 MIGRATIONS TO delivery_app", although nothing was pending and every role membership was already granted.

## Cause
Both gates ran unconditionally. A typed gate guards a change; with no change it is a manual ritual.

## Fix
- `apply-pg-cutover.py`: no pending migration and no missing role membership prints "0 migrations pending ..." and continues. A pending migration or a missing membership keeps the typed gate. Partial migrations still refuse.
- `req migrate --check`: dry review, exit 0 = nothing to apply, 3 = work pending. Changes nothing.
- `rebuild.py` step 5 uses it: exit 0 logs "0 migrations pending" and moves on; exit 3 keeps the existing gate; any other exit stops.

## Siblings checked
Cleanup already skips its gate when nothing is old ("nothing old to remove"). The other typed gates (reset, stop, stop-all, cost acknowledgements) each guard a real action the owner chose to run.

## Rule
Ask only when there is a change or a judgment. Never ask the owner to type a count of zero.
