# 2026-11-06 - Poll quiet progress less, but wake on real commits

A held answer inherited two independent one-second DB loops: app progress reads
and child stop/auth/generation checks. Longer model waits made the POST's query
count grow. Removing the security loop or slowing it would change control safety.

Keep that loop unchanged. Back off only app progress safety reads: 2, 4, 8, 15s,
capped at 15s. A private nonblocking wake-only datagram after child journal
commits wakes an owner-scoped read immediately. Snapshot the wake version before
reading so a commit between read and wait cannot disappear. The final drain and
transient-DB survival remain. A wake carries no event content or authority.

Measurements with real HTTP, disposable PG, real child and 15s fake provider:
- Before: 288 counted POST queries, 18 app drain calls.
- After: 274 counted POST queries, 8 app drain calls.
- Local DB milliseconds vary and are not a remote latency prediction.
- Real observational journal commit during >=8s backoff ingested in 29ms.
- Stop detection baseline 0.640s / after 0.755s; auth revocation baseline
  0.762s / after 0.736s. Same 1.5s test bound, same one-second control loop AST.

Savings at 15s are modest because the independent child control checks dominate
and are intentionally untouched. Tests must show this rather than claim that
all POST query growth has disappeared. Lost wakes fall back to a <=15s safety
poll; ordinary commit signals and the final drain remain prompt.
