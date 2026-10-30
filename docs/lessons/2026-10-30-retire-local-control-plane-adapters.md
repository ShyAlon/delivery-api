# Delete adapters after proving every native consumer

The PG store is independent of the old local class and owns its operations.
The public Storage protocol and phase projection remain. The local schema,
local adapter, replica subclass and now-unused libSQL row/connection module are
removed; the factory accepts only PG. Dead local database path and replica
configuration fields are removed too. No remaining source/test caller used the
libSQL module after the app selector was removed. The no-local-file/isolation
assertions now verify removed fields and the same former path remains absent.
The application no longer selects the replica directly.

The local adapter included password/OAuth-state/opaque-session storage methods.
They are purely tied to the retired backend: PG middleware rejects the old
OAuth/email-availability routes, the four permitted auth routes delegate to
Supabase Sessions, and auth.actor returns the verified Supabase identity before
local-cookie access. No auth.py, Supabase auth, OAuth helper, session helper or
email-availability route implementation is removed in this change. Their
existing PG refusal/session checks remain.

Tests cannot monkeypatch a deleted class. Constructor/method poison checks now
prove the class is absent, PG has no parent, and named methods belong to PG.
The existing bridge poison and all operation/value/isolation assertions remain.
A blanket sqlite3.connect poison would also block the engine's separately
retiring reference journal, so adapter absence is the appropriate scoped proof.

Coverage retired explicitly: evidence_store no longer runs its three shared
contracts against the deleted local backend. Progress replay/gaps/snapshot,
review/draft/revision/audit, and feedback CRUD/actor-boundary contracts still run
unchanged against restricted PG. No test function or behavior assertion is
deleted, and no skip is added. Old-backend reference execution is not claimed
retained. New fail-first factory checks reject every former local address type
before connection. Existing reopen/persistence checks still use the PG factory.

The PG-required bootstrap (#511) is verified merged. This branch was rebased
onto main a7df1bf with #512/#513 also merged. Post-rebase gate: 168 pass, 35
explicit lifecycle-matrix deselections; collection: 3471. Its PG operation
replacements are already merged. No live database changes,
data reset, deploy or spend. Flake tracing remains. Hosted CI unverified.
