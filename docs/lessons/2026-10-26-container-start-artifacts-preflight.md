# 2026-10-26 - Preflight the writable paths at container start

Incident: a preview container served for ~5 hours on a stale /data bind mount
(its host temp dir had been deleted). Everything that avoided /data worked -
API, database, sign-in - and the engine log tee kept appending through an
already-open fd. The first engine start died in the stop fence:
FileNotFoundError('/data/artifacts'), and the task page showed only "Session
unavailable in this environment".

Lessons:
- A container that cannot write its artifacts root is not started. Probe the
  exact operation the run depends on (mkdir -p + a real write) at launch and
  refuse with the cause, instead of letting the first user action discover it.
- "The mount exists" is not "the mount is healthy": a deleted host directory
  leaves a zombie mount that answers some calls and fails others. Only a
  write probe tells the truth.
- Graceful degradation (the log tee's "never fatal") is right for diagnostics
  and wrong for required state - decide per path which one it is.
