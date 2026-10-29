# Anonymous telemetry success is part of a clean boot

The tenant boundary now allows anonymous client-log POST. The preview helper
still tolerated its old 401. Remove that obsolete exception and capture
successful telemetry separately from HTTP errors, requiring at least one 2xx
POST during the collected boot. Missing telemetry is a failure, not an empty
error list that happens to pass.

Three fail-first failures / seven passed in 4.18s: actual anonymous boot no
longer matched old tolerance, telemetry 401 still passed, and absent success
was accepted. After fix: ten passed in 4.00s current; ten passed in 4.27s held PG-first.
Real-boot test gains a positive success assertion; no existing assertion removed
or weakened. All other error classification tests retained, with two new cases.
No deploy or bootstrap flip. Hosted CI/full suite unverified.
