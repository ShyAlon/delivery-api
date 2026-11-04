# Database-only generator input manifests

The child generator manifest accepted an optional document store. Omitting it
silently read host files instead. That made input authority depend on the host.

Require the owned document reader. Canonical paths remain namespace identifiers,
not a second source of input bytes. Missing retained bytes fail closed.

Regression: a present file without a document reader was accepted before this
change. The new test fails on that version and passes with the requirement.
The manifest tests also prove host-file replacement cannot change its digest,
retained-byte changes do change it, and missing retained bytes are refused.

Local validation: 13 child-generator tests passed, including actual child and
restart journeys. CI unavailable, local gate only. This is an incomplete WIP
checkpoint, not a merge-ready theme or deployment approval.

## Canonical producer/revision readers

Fresh-input, phase-evidence and retained-proposal helpers still selected file
implementations using a store attribute. Retain only their PG implementations.
Real-PG tests replace obsolete descriptor-race tests with hostile/absent folder,
required-byte, bounds, owner, generation and proposal identity checks. Shared
descriptor helpers remain for other callers until their separate audit.

The policy regression failed in all three readers before removal. Rebuilt PG
reader/journal group: 33 passed. Revision-stage/job/intent cross-suite: 27 passed.
A combined PostgreSQL store run reached the command timeout without a final
result; it is not passing evidence and must be rerun in smaller groups.

## Artifact presentation and downloads

Seven read/export endpoints retained backend-selected host readers even though
PostgreSQL already supplied their production implementation. Remove only those
explicit function-scoped legacy paths. Keep snapshot hash reads for ZIP members,
so a concurrent revision cannot mix ZIP bytes. Temporary package construction
is scratch space, not persistent authority, and remains unchanged.

All seven policy cases failed before the change. Route/byte/package tests: 66
passed. Added a real authenticated route regression for absent and hostile host
folders: exact download bytes, view JSON and ZIP bytes must agree, while a second
owner sees 404. Full theme gate is still pending.
