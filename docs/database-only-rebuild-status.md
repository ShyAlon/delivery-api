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

Unit 6: imported recording rows/artifacts retained in PG; replay survives source
folder loss. HTTP upload staging is temporary and cleaned. Replay/import selected
group: 14 passed. Public replay remains guarded, unchanged.

Unit 7: parent runner requires registered child and PG evidence, with no file
pricing or publication fallback. Actual child + installed all-phase fake-provider
journey + fresh zero-credit startup group: 17 passed. No live model call.

Unit 8: signed tenant identity only; no local admin/basic/OAuth fallback. Signed
identity, routes, sessions and operator-startup group: 84 passed. Remaining
compatibility tests still need conversion; no full-gate claim.

Unit 9: gate decisions/lineage use PG transactions, not host paths. Signed gate,
revision, stack and identity group: 51 passed. Unit fixtures queue only; provider
execution proof remains the separate installed journey.

Unit 10: installation coordinator uses PG destination lock, anchor and promotion
receipt only. Policy regression failed first; 40 journal/plan/safety/staging tests
passed, plus 5 real PG installation/crash/tamper/rollback scenarios. The old
filesystem helper module remains to extract its pure plan validator then retire.

SDK fixture gap resolved: fake wire tests now provide a real saved operator policy
and matching 10^12 quote unit scale. All 8 SDK variants passed. This changes only
disposable test policy, not production admission. No live provider contact.

Unit 11: supplemental classification, execution and provenance readers use PG
only, with no host debug glob, pinned-route file or unbound session-price read.
Five policy regressions failed first. Signed classification browser included in
71-test provenance/documents/debug/policy group. Chromium installed locally for
remaining browser gate. Full gate attempt was interrupted at 15%; no full-pass
claim, known compatibility fixture failures remain.

Unit 12: removed host bundle discovery/root settings/projection. Retained bundle
summaries preserve actual pending/error/warning/approved interview states and
project title, completion and validation from PG. Red-first retired-function
policy; 5 selected bundle/session tests passed, signed routes included.

Unit 13: retired restore/backfill modules, backfill entrypoint and file publication
helpers. Their tests were removed with the retired behavior, not skipped. Current
PG validator provenance and settled changed/vanished/appeared/current/identical
cases replace the old file tests. 94 document/manifest/byte/policy tests passed,
including failed publication atomicity and worker-attempt fencing. No import or
migration route remains in these modules.

Unit 14: extracted the pure exact installation-plan validator into the plan
module and removed the unused file-lock/anchor/rename installation module.
Removed uncalled approved-upstream file readers; PG source/stage tests own that
contract. 40 revision plan/journal/safety/stage/policy tests passed. No route or
installation authority is added.
