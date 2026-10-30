# Metrics have one durable backend

The PG resource sampler, restricted writer, summaries and maintenance are merged.
The old file schema, connection state, local queries and retention loop are now
removed. Samplers and readers use PG only. An unsupported DSN/backend fails
before any local open; failed writes remain visible without stopping a run.
Sampling, CPU/memory calculations, host identity, retention, operator scope and
bounded PG reads remain unchanged.

Tests previously inserting local rows now use the real restricted PG writer.
All CPU percentage, peak growth, session/phase identity, rollup average/max/count,
raw deletion, straddled cutoff, idempotence, whole-day rollup, timeline, error,
no-money-wording and real-process assertions remain. The direct container-row
inspection uses the owned disposable admin fixture, not production access.
The write-failure fixture injects a PG connection failure instead of an invalid
local directory. It retains the visible-not-stored and no-raise contract.
A fixture that used now plus five days now starts ten days in the past, retaining
its relative retention expectations while respecting the existing PG maintenance
bound against future timestamps. The old explicit-local-backend expectation is
replaced with a refusal, an intentional removal of that capability.

No test function deleted, no skip added, no behavior assertion relaxed. Scoped
sqlite3.connect poison remains an independent test guard, not a dependency on
an exposed implementation import. Fail-first absence and invalid-profile tests
preceded retirement. The metrics browser test is retained.
No live DB changes, deploy, spend or data reset. Flake tracing remains unchanged.
Hosted CI remains unverified.
