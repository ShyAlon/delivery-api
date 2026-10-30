# A new smoke dependency must be wired through its real callers

## Failure

The PG-only cutover correctly made full-smoke refuse an implicit database and
added an owned disposable-PG supervisor and test-only image. Build/preview/deploy
callers still invoked full-smoke directly on the service image, so every fresh
build failed before startup. Stub Docker tests returned success for any command
containing full-smoke.py and hid the missing supervisor boundary.

## Fix and prevention

Build, image reuse, preview and server smoke share one helper that creates the
test-only derivative, runs the actual candidate entry with owned migrated PG,
then removes the derivative. No caller forwards a live DSN or provider key.
Fail-first tests cover every call site and execute the helper against a recording
Docker double that verifies derivative build, network-none supervisor invocation,
platform propagation, cleanup and failure propagation. The real owned-PG/full-entry
proof also runs outside Docker. Neither mock calls nor that proof establish an
actual container build or architecture compatibility; report those separately.

Scan the following steps when a dependency changes: step6 reuse had the same
missing supervisor; step7 read-only live DB/startability has an explicit selected
DSN; step8 derives the engine DSN from that app DSN and disables file sessions;
step9 is anonymous HTTP/browser-only and does not need a disposable DB itself.
