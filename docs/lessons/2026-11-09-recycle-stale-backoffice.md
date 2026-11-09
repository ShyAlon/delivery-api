# 2026-11-09: rebuild aborted on its own leftover Back office

Problem: `req rebuild --apply` reached a healthy preview, then failed with "Back office cannot own its private port (port 8020 is held by: Python ...); existing listener untouched". The holder was a Back office from an earlier preview run that was never closed.

Cause: PrivateBackoffice only waited for the port. It had no way to tell its own leftover from a foreign listener, so it refused both.

Fix: on a bind failure, if the port answers /health as role=backoffice and lsof names exactly one listener pid, send SIGTERM once and keep waiting for the port. Any other listener is still refused and named, untouched.

Test: test_stale_own_backoffice_is_recycled_not_a_failure (fail-first: it raised "existing listener untouched" before the change).
