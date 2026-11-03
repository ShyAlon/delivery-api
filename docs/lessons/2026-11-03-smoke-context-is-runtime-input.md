# Test the actual minimal context

Adding a harness dependency without copying it into the Docker smoke context broke owned-PG setup before the service checks. Full-checkout CI could pass because the file existed there. One explicit context preparer now supplies smoke and its CI regression; CI migrates disposable PostgreSQL from those copied files, not the whole checkout. The test-only Dockerfile copies the runtime inventory too. Never paper over this by granting wildcard roles or borrowing a live database.

Build ABORTED summaries must repeat the observed failing check. Referring to "output above" loses the cause when the owner pastes only the tail. No diagnostic line means say it is missing and retain the exit code, not an invented diagnosis.
