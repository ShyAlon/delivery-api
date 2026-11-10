# 2026-11-10: not every admission SELECT is read-only

## Problem
After the owner lock, reserve read suspension policy, limits, and the existing attempt in three separate network syncs.
They are independent reads. The later totals SELECT looked like a fourth read that could join them.

## Finding
`requi_admission_totals` takes locks and updates admission projections and expiry counters. Eagerly running it during
replay or before configuration/refusal checks would change behavior. It stays conditional and outside the read batch.

## Change
- Pipeline the three independent reads after the existing locks and authority checks.
- Validate the returned suspension row with the same action policy used by the ordinary single-read guard.
- Return rows only after the entire batch synchronizes successfully. Failures propagate and roll back the operation.
- No authority data is stored for a future call. No locks, caps, charge logic, transaction boundaries, or schema changed.

## Measured
On clean main, reserve costs 14 syncs; this change costs 12. These are 2 syncs saved per reservation, not the larger
reduction estimated before inspecting the totals function. This is separate from the V1 grouping change.

## Tests
A fail-first count test, replay/conflict that must not call totals, and a real PostgreSQL mid-batch error/rollback test.
The existing cap, owner-isolation, lock-wait/revocation, prepaid and broker tests also run.

## Not verified
Live Supabase pooler behavior was not tested. The batch runs within the existing scoped transaction on one connection;
it does not pipeline BEGIN/COMMIT or retry a partially executed admission.
