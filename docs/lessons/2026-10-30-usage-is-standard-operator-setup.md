# Usage reads belong in normal operator setup

The private setup retained four read capabilities while Debug always collected
normalized task usage, which separately requires usage.read and its fixed read
role. The resulting tenant404 obscured a missing operator permission.

Owner asked for future sessions, not a past-task patch. Added one additive
standard migration, a future-registration default, the usage role in normal
owner apply/readiness, usage in default private setup/checks, and standard setup
at the end of confirmed migration apply. No separate repair command. The owner
still applies/reviews DB changes and separately confirms private settings save.
No live operations performed by the implementation work.

Fail-first tests reproduced the masked404, a collector misclassifying an
unavailable result as available, and standard setup dropping usage. Tests retain
cross-owner/revoked opaque denial, verify real migration/default/grant behavior
on disposable PG, and preserve non-usage permissions. A wider test wrote old PG
logs while asserting the file-log route; switched its evidence fixture to real
file writes/drain, retaining exact task-isolation assertions.

Never infer permission from an admin badge. Detailed missing-permission results
are returned only after a live registry-backed Debug read check. Do not treat a
local file save and a DB migration as one atomic operation: declined/failed save
leaves applied migration visible and can be retried in the normal flow.
