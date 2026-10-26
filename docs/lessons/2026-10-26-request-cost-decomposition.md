# 2026-10-26 - Measure where a request's time went before tuning anything

Incident: during a live interview turn, task polls took 12-14s with only 1.5s
of DB time; the request log could not say where the other ~11s went, so every
theory (pool, engine, event loop) was unfalsifiable.

Lessons:
- A request's cost is a decomposition, not a number: db_ms, engine_ms,
  checkout_ms, auth_ms and held_ms (the pin window) each get their own field,
  or the biggest term hides inside the smallest one.
- A sync DB read inside an async SSE generator blocks the whole event loop
  and appears in NO request's log line - instrument the loop, not only the
  requests (stream cycle timing + a loop-lag monitor).
- One transaction per read request pins a pooled connection across everything
  the request does, including slow engine HTTP calls. held_ms makes the pin
  window explicit before anyone "fixes" the pool size.
- Add the timers first, run the real workload, then fix the measured term.
- The numbers belong where the operator looks: a bounded rolling window behind
  the Debug dialog (per-type p50/p95 + slowest recent, route templates instead
  of raw ids) and a text rendering in the everything ZIP - not only in a log
  file on the machine that is misbehaving.
