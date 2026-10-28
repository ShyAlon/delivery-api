# Local link attribution cannot be ported by bypassing PG ownership

The legacy /runs route attributes actor local. PG requires the authenticated
owner and correctly rejects this. Keep local lifecycle/budget assertions on an
explicit legacy fixture for auth retirement reconciliation rather than mocking
the actor or weakening owner checks. Independent settings/session/preflight
fixtures can move to scoped PG unchanged. Name the incomplete local contracts
instead of claiming the entire API module migrated.
