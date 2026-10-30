# Validate shared sockets for every borrower

The delivery retry existed but only handled connections marked reused. The engine uses the same pool and does not consume delivery's diagnostic first-use flag. An engine-used dead socket could therefore bypass delivery's retry; engine's own borrower could fail too. Both paths reproduced on owned disposable PostgreSQL before edits.

Use psycopg_pool's public empty-query check at shared checkout, before any caller SQL. The driver discards broken sockets and bounds replacement by checkout timeout. This covers both public borrower paths without replaying business transactions or changing pool size. Existing transaction-local tenant claims remain. Failure during caller statements or uncertain COMMIT is still visible and never replayed.

Tradeoff: one protocol round trip per checkout, even healthy ones. A20checkout test with10ms injected check delay costs at least200ms extra; healthy-check overhead is not free and can matter with remote DB. This is per checkout, not per SQL, and should be considered alongside page batching. Do not hide it with idle-only validation that leaves newly dropped sockets unprotected. A disconnect after validation is still possible; no check can guarantee future transport survival.

This does not prove why the owner's historical server closed TLS, or replace all pre-business setup failure paths. It demonstrates a reproducible escaping stale-socket path. No broad provider/request/write retries, infrastructure changes or live probes.
