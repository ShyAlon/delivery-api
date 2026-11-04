# Database-only storage work in progress

Base: merged main f23feb2 (runtime parity #671).

This branch is a recovery checkpoint, not merge-ready. No deployment, server
contact, migration, or paid execution is part of this work.

## Current evidence

- Unit 1: generator manifests require owned retained documents, with no host-file
  fallback. Failing regression reproduced before the change.
- Child generator suite: 13 passed on the rebuilt code.
- Unit 2: canonical producer/revision readers have no file fallback. PG reader
  and journal tests: 33 passed; revision stage/job/intent: 27 passed.
- Unit 3: artifact snapshot/output/view/download/ZIP and session list/bundle
  endpoints use only PG. Seven policy cases failed before removal.
  Route/byte/package group: 66 passed. Additional authenticated hostile-folder
  regression is included.
- Full PostgreSQL store run timed out without a final result and is unverified.
- CI unavailable, local gate only. Full theme gate has not run.

## Still to audit and implement

- Artifact reads, downloads, bundle exports and settled publication must use PG.
- Remove restore/backfill and filesystem publication paths once replacement
  coverage is verified. No old-session import or migration.
- Fresh producer inputs and revision evidence/proposals/install must be PG-only.
- Retain recording imports and replay bytes in PG; discard upload staging.
- Customer diagnostics must read retained evidence and preserve privacy.
- Remove local-auth and location-selector branches without bypassing signed
  identity, tenant RLS, owner admission, balance, ceilings, Stop or quiescence.
- Retire only obsolete tests, replacing them with current real PG guarantees.
- Full local core, PostgreSQL, installed-runtime, fresh-start, browser and build
  gate; final diff review before opening one theme PR. The owner merges.

Previous environment results do not count as proof for this rebuilt branch.

Unit 4: retired host folder/interpreter setting routes, 57 signed route/policy
tests passed. The underlying uncalled module awaits dead-code removal with its
remaining legacy tests; no runtime setting is written by HTTP anymore.

Unit 5: coordinator uses PG documents, fence and publication unconditionally.
31 non-SDK coordinator/recovery tests passed; 66 child/document/fence tests passed.
Eight SDK transport variants remain unresolved: first failure is the fixture's
missing operator expense policy (also reproduced before this edit). Production
money checks remain intact. Full gate is not green and no PR is ready.
