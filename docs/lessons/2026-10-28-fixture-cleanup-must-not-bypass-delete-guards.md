# Fixture cleanup must not bypass delete guards, 2026-10-28

A progress test leaves a running task. Production rightly refuses deleting it.
Do not force terminal state or delete with a superuser to make teardown green.
In a disposable PG server, revoke the test auth session, preserve running/accounted
rows and their history, and let process cleanup destroy the whole server. Catch
only exact known retention refusals and fail loudly on unknown errors. Prove that
state remains running while the session is revoked.
