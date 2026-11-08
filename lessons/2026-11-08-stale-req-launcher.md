# The req launcher is a copy, and copies go stale

The repo's scripts/ops/req never updates itself - it syncs the checkout, not
the launcher on PATH. An installed copy older than a newly registered owner
command answered `req rebuild` with "unknown command" and named Downloads
scripts, sending the owner to the wrong fix entirely.

An error that can be caused by a stale copy of the thing printing the error
must say so. The unknown-command branch now compares the running launcher
against the repo's runner and, when they differ, appends the one repair
command (bash .../scripts/ops/req-install.sh). When they match, the message
stays exactly as it was.

Any CLI whose installed form drifts from its source of truth needs its
dead-end errors to detect the drift and name the repair - never leave the
user holding an accurate-sounding error that points nowhere.
