# Startup preflight: one readable report instead of a traceback hours later

2026-10-26: a container served for hours on a stale data mount; the first engine
start died with FileNotFoundError and the task page showed only "session
unavailable". The failure was provable at startup. The fix pattern:

- Verify foundations BEFORE spawning processes, and say the result as
  checked / expected / found / where. "0 found" from a filtered view is a lie:
  the unsettled-compute-segments check is owner-scoped under row security, so at
  startup it honestly says SKIP and the Debug dialog evaluates it live per
  signed-in owner (from the already-loaded credits ledger, never a new query).
- Database probes run as the app's own restricted login with read-only SELECTs.
  The claims probe replays exactly what request.jwt.claim does (SET ROLE +
  session claims, then auth.uid()), so a grant that would break the first real
  request breaks startup instead.
- Required tables are pinned to the migrations on disk by a test: adding a
  table without updating the preflight list fails the build, not a user.
- The launcher pins the build it expects (REQUI_EXPECTED_REPOS) so the running
  container can say when the image is stale - a class of confusion we have
  burned hours on twice.
- The report is written next to the engine log and read back into the Debug
  dialog (Startup Checks) and the everything ZIP, so "why did my task fail" is
  one click, not a ZIP export and a traceback.
- Refuse only on foundations that cannot serve a request (artifacts root,
  role/claims, required tables, engine compatibility). Warn on degraded ones
  (pool budget, provider key, unsettled segments, stale build): blocking on a
  warning bricks recovery paths.
