# A read must release its connection before calling a shared-pool engine

Owner reported30s checkout timeouts; actual running build was not established. Do not infer ten occupied business sockets from PoolTimeout: opening/replacement/validation failures also time out. The old error asserted a full pool without gauges.

Reproduction on owned PostgreSQL: ten response-lifetime shared read transactions consumed ten slots and waited for the engine; the same-pool engine's next checkout timed out. This does not require a leak. Session-cache misses separately stampeded: twelve requests for one verified owner/session made twelve liveness checks.

Keep SQL reads batched before the engine boundary, then finish the presentation read transaction before the engine client sends. Later store calls use normal short transactions. No global disabling of shared reads, no answer/accounting transaction changes, and no larger pool or wait timeout. One in-flight liveness check per verified owner/session; preserve start-anchored TTL, negative caching and failure propagation without caching failures.

Pool timeout diagnostics must come from shared public checkout hooks covering engine and delivery: allowlisted gauges, borrowed count, oldest ten borrower ages and fixed category only. No DSN, identity, SQL, prompt or parameters. Remove diagnostic borrower records before handing a socket to its next borrower. Unknown category is honest: engine-or-background, not a guessed operation. Exception-path request timing and failed session-check auth timing must survive errors. Diagnostics do not issue database queries.

Tests fail first for duplicate liveness work, shared-pool engine starvation and missing timeout diagnostics. Ten-reader real-PG after-test completes with all ten connections available. Four reads before the engine still use one setup/transaction. Existing clear-timeout-message assertion changes deliberately to stop claiming proven saturation; FakePool follows observed subclass, callback assertion retained. No tests retired.
