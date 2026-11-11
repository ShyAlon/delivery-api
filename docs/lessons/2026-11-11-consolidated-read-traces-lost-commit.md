# Consolidated fast reads must still name their transaction boundary

The combined read optimization exposed two failing assertions before publication. The old trace contract expected a savepoint for a plain read. The new pipeline really contains BEGIN and COMMIT, so dropping their names made the trace less useful even though it correctly counted one Sync. Record both boundaries with zero additional Syncs and assert the total before request logging consumes the trace.

The route-isolation test also reproduced on main: when no model pins exist, route_errors stayed empty rather than explaining the unavailable configured providers. Set those reasons before raising the existing NoApprovedModelPin. Do not invent a model, price, or fallback.

Consolidation keeps generic reads, pin lookup, timing chunks, trace attribution and rebuild friction. It intentionally leaves out temporary per-provider admission batching and the inactive charge migration. The full two-touch lifecycle remains separate unfinished work.
