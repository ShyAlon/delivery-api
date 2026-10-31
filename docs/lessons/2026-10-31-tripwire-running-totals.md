# Tripwire evidence is a separate projection from admission

Confirmed-expense guards used lifetime session sums, owner unknown checks and repeated rolling sums under the global money lock. Keep exact private bucket totals, plus per-attempt contributions with indexed next-change times. Future settlements remain unverifiable until their time arrives; expiry is strict at 24 hours. Scale validity is a separate count per exact historical scale, so changing an operator profile does not require pretending old rates match. Unknown, missing-time and null-expense evidence remain distinct from zero.

Keep the guard's authorization, reason order, thresholds, episode/outbox identity and accepted settlement behavior unchanged. Every relevant ledger write, user creation/deletion and explicit job attribution correction maintains the same projection transactionally. Acquire the existing global lock before owner admission locks. First use and backward clock perform an explicit retained-history rebuild; routine calls touch only due entries. Retained ledger remains authoritative, at the cost of extra projection storage/writes.

Rollout is owner-reviewed schema work. The existing migration helper must refuse this file's private cache DELETEs until separately approved. No borrowed exception, runtime schema install or live database action.
