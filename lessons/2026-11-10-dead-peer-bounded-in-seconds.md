# One stream read took 59 s: a dead connection, not a blocked event loop

Date: 2026-11-10

Evidence (Shy's 07:50 ZIP): `stream.events.slow dur_ms=59405` on an idle app; the same stream's other 32 reads took ~0.8 s;
`loop.lag` stayed at 1.1-1.4 s. The stream reads run in the threadpool, so the logged `loop_blocked_ms=84387` is summed
THREAD time, not event-loop blocking (the field name and an old comment said otherwise; comment corrected, names kept).

Cause (strong inference, not reproduced with real packet loss): 59.4 s equals the pool's keepalive budget, idle 30 s + 3 x 10 s.
A connection whose peer vanished without closing hangs the next check or statement until the kernel gives up.

Fix: DEAD_PEER_KWARGS on the shared pool: `tcp_user_timeout=10000` (bounds unacknowledged data; slow queries still ACK) and
keepalives 10/3/3 (19 s idle detection). Verified: libpq applies them on a real TCP socket (TCP_USER_TIMEOUT 10000,
KEEPIDLE 10, KEEPINTVL 3, KEEPCNT 3). tests/test_pool_dead_peer_bound.py pins the config (failed first: key missing).

Siblings, not changed: one-shot payment connections (money path: aborting mid-transaction changes outcome semantics, owner call),
startup role checks, interview-engine `connect_claimed` (standalone engine only; hosted engine uses this pool).
