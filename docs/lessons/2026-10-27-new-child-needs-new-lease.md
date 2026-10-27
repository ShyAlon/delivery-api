# A fresh child cannot reuse a parent's live lease

Phase-1's epoch was only a proposed wire field. Grounding found engine leases
and worker-job attempts are distinct. Compare published evidence under a row
lock, reject a live holder, and acquire a new epoch before constructing any
graph. Restore failures must release even if no session was registered yet.

Separate the schema from delivery's package before the engine imports it. A
new dependency must travel through clean installs, locks, CI, Docker and dist
builders too; a passing editable environment is not sufficient evidence.

Missing supervisor and engine restore modules failed tests first. Further
oversize-frame and parent-reset regressions failed before their fixes. Keep
paid subprocess refusal until money/recovery works, never mistake reap for
provider reconciliation, and never use automatic fallback to replay paid work.

A separate hidden-red finding is recorded in engine's
2026-10-27-debt-claim-transport-hidden-red.md. Enabling its optional sibling
migration source makes the checkpoint claim-transport test fail identically
on old main. The test discards transaction claims when rebuilding a pool from
conn.info.dsn. Preserve its assertions and fix the harness in a separate item.
