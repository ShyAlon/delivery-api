# Delete the pre-cutover manifest read switch

PG reads already return through the authoritative publication branch before the
old rollout switch. The old non-PG cloud fallback and enable-readiness helper
could not control the shipped PG path. Delete that duplicate branch and helper.
Download/render use the same PG authority rather than a retired flag.

Keep integrity failures, missing-ready-revision refusals, tenant checks and the
byte-backed publication path. Existing real PG tests cover flag-on and flag-off
publication, exact bytes and no filesystem fallback. Do not delete live data or
remove source-file recovery tooling without separately tracing its remaining
producer callers. The old config symbol remains for tests/config compatibility,
but no app read depends on it.
