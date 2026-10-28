# Shared PG fixtures need unique engine session IDs

Separate SQLite files hide globally unique session IDs reused by fixtures.
Per-test PG tenants do not change the engine_sessions primary key: scope the
fixture session ID to its unique owner instead of deleting another fixture's
session or relaxing uniqueness. Keep real begin/reconcile and claim-once
operations so admission regressions still test the production state machine.
