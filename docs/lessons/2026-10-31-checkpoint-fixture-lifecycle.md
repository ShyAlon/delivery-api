# Test jobs need teardown, even when Stop keeps their evidence

Generator-checkpoint tests left claimed fake jobs running. Owner-wide transition tests correctly refused those jobs if they ran later. Track each checkpoint test's enqueued jobs in a shared fixture and close only those jobs in disposable PostgreSQL in finally, including failed/revoked/Stop paths. Do not reorder tests or relax the production owner-work guard. Retain task/checkpoint/accounting evidence and every assertion.
