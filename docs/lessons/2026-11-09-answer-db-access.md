# 2026-11-09 - The answer POST's query storm was a stop-watch, and the "missing" wake already existed

Production turn evidence (turns 8/9/10): answer POST 396 queries / 104s db_ms of a 116s request.

1. ~300 of the 396 queries (75%) were the child handler's 1Hz stop/auth/generation
   watch: three store reads per second of child life. It never fired once.
   The cadence is now a named constant (_STOP_WATCH_SECONDS=5.0), pinned by a
   static-invariant test because the real-child budget suite is environment-broken
   in the sandbox. Stop reaction goes from 1s to <=5s; the fence+SIGTERM stop path
   is unchanged.
2. The anatomy report claimed the progress watch polls blindly because the child's
   journal commits never reach the in-process event bus. WRONG: the pipe wake
   (child_progress.py progress_wake_channel / install_progress_wakes) already
   re-signals the bus on every committed journal append, and it works in
   production - all six ingests across the run show source_age_max 5927-6122ms,
   a constant transport cost (fenced commit ~1.9s + fetch ~2.8s + ingest ~1.3s),
   not random backoff lag. The backoff polls in the logs are the safety net
   during the by-design silent LLM call (heartbeat_signal is a deliberate no-op).
   Lesson: before building a "missing" mechanism, re-read the container evidence
   with the existing mechanism in mind - the same care as verifying a "dead"
   emission site before removing it.
3. The real remaining cost was the read itself: two checkouts per progress fetch
   (claims setup + round trip each at remote-pooler latency). Engine-side
   PgSessionStore.progress_page now reads publication + journal in one checkout.

Wall time is unchanged (the child bounds the turn); this cuts DB load and pool
pressure: ~240 fewer statements per answer from B1 alone.
