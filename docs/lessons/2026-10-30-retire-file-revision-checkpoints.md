# Retire file checkpoints only after the PG workflow exists

The required PG profile no longer uses the file manifest/checkpoint reader,
file stage writer or copied continuation database. All seven revision modules
now select the existing PG snapshot/lineage implementations. Owner, generation,
settlement, explicit cost acknowledgement, fresh gate and digest checks remain.
No route, cloud fence, approval policy or provider permission is opened.

Tests must not create local databases as incidental setup for PG workflows.
The shared real-stage fixture seeds the source in real restricted PG and builds
non-durable graph state in MemorySaver. Production staging remains real PG.
The 35-case retained lifecycle matrix is unchanged, including lineage, auth
revocation/expiry, swapped snapshots, settlement, stop/child control and rollback.

New direct PG writer cases cover success/retry refusal, no/foreign writer fence,
source byte drift, proposal digest drift and options drift. Source checkpoints
stay unchanged and no new execution binding appears. PG stage receipt identity,
lineage typed-generation and owner/installed/dispatch tamper checks are added.

Retired test executions, approved with the retired file capability:
- test_revision_stage_evidence: exact file manifest receipt; file-drift, missing,
  symlink, manifest-drift, lineage-drift; active checkpoint sidecar; directory
  swap; composed pinned-reader wiring; document/channel mismatch; equal JSON
  with different staged bytes. Eleven expanded executions. No file manifest,
  sidecar or pinned-descriptor capability remains or is claimed covered by PG.
- test_revision_stage_real: exact file stage/source preservation; file writer
  preservation/no-install/retry; no_fence, foreign_fence, source_bytes,
  proposal_bytes, escape, symlink_stage; partial-file retention/no-auto-retry.
  Nine executions. File copy, filesystem stage namespace and partial-file
  recovery checks are gone with that implementation. Shared PG fixture remains.
- Independent approved-upstream exact byte and typed-ConOps tests retained.

The first absence test failed on the file driver import. Initial broad test run
was interrupted at 120 seconds and is not a pass claim; missing engine source
on that invocation was fixed by including the engine source for integration.
No skips introduced. Hosted CI unverified. No live data reset/write, deploy,
spend, auth helper retirement or flake-tracing removal.

Post-#514 rebase found a real default-profile regression in child startup:
child_generator_entry imported the pure manifest validator from the parent
adapter, eagerly importing required service config despite the child's strict
no-credential environment. Move the identical validator to generator_manifest,
re-export from the parent and import directly from the child. No allowlist,
path/digest assertion or child environment rule changed. Existing child needs
case failed before the extraction; child/process integration 33 pass after it.
A subprocess import regression proves no config/parent import without credentials.
Replacement PR targets main after #514 ancestry, superseding stacked #515.
