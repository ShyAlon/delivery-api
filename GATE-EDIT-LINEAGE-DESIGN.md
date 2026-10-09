# Gate/Edit semantic lineage and execution sessions

Status: architecture chosen; private planning only. Public commit/restart remains unavailable until reviewed installation, a durable promotion journal and founder design review are complete.

## Decision and provenance

On October 9, 2026 at 15:10 IDT, the engineering coordinator chose Path A under the delegated recovery architecture work: preserve immutable semantic/source-session lineage and use a fresh execution-session namespace for each regenerated generation. This is an implementation decision under delegation, not a new founder grant or approval to deploy.

Product-semantics source: Shy's original WhatsApp message at12:18:17 IDT, id `wamid.HBgMOTcyNTQ1Njc4MzMwFQIAEhgUM0I4NUVEMzQzRDJGNEJBMDRGNDUA`: "If that decision affects other decisions and descriptions in that document, we have to regenerate it. Maybe we should indicate to the user that it will incur extra cost." This requires regeneration rather than hand-patching. It does not independently prescribe session storage, authorize paid execution or approve this design.

Path B, retaining the old session while adding generation-specific control paths, was rejected because it changes the established same-host Stop-marker topology and blurs source evidence with new execution identity.

## Identity boundaries

- Semantic session id is the id already present in the reviewed IntentModel and upstream contracts. Those approved documents and their bytes stay unchanged. New generated documents retain semantic trace ids rather than restamping approved inputs.
- Execution session id is a fresh server-owned namespace and checkpoint thread. Its immutable owner/task/generation binding is separate from the semantic id.
- Parent execution session and generation identify the source run; the lineage record binds its checkpoint, exact reviewed artifact evidence, retained proposal document digest and fresh destination execution session/generation.
- A lineage plan is not a session binding, owner approval or promotion receipt. Names and boolean flags never substitute for authenticated review.

For repeated edits, the semantic id stays at its original value while the parent execution id advances. Do not infer semantic id from the latest execution id.

## Installation ordering, still unimplemented

Prepare new files in an isolated destination. Verify the source under same-host writer exclusion and compare current owner, policy, task/gate/draft/checkpoint/artifact/proposal evidence. Persist an immutable reviewed installation intent before promotion. Fsync files and destination directories; record exact staged content digests. Do not overwrite the old run.

A filesystem and database cannot share a native transaction. Use a journal with explicit stages and compare-and-swap of the database's active-generation pointer only after staged files and checkpoint readback are verified. Interrupted work leaves an explicit held/recovery state. Never replay a paid worker automatically after ambiguity. Recovery must reconcile the journal, destination files and database state before any explicit resume.

Only reviewed installation may create the fresh immutable engine-session binding and activate the next generation. Reset the edited gate and affected downstream approvals; unchanged upstream evidence remains traceable through lineage. The new session has its own Stop marker initialized for the new generation; do not copy a source Stop marker. Existing provider holds remain attached to original jobs until actual settlement. Quiescence must include surviving provider threads, not only graph completion.

## Remaining integration

Separate semantic validation from execution checkpoint configuration in generator adapters. Validate every source contract against the bound semantic id, every execution job against its execution id and generation. Owner/task lineage must be stored and validated at the capability and engine boundaries without optional fallbacks. New execution sessions must not expose new interview mutations against preserved source contracts. No general cloud rerun opening as a side effect.

Typed artifact validation, founder-driver checks, proposal review, extra-cost acknowledgement, per-call admission and draft-only Save remain required. Other phase adapters remain unsupported until their own input/trace semantics are implemented. No public or live execution is enabled by this document.

## Execution-generation anchor

Both current StopFence implementations treat an absent marker as generation1 and refresh only requested=true markers. Do not write a requested=false Stop marker to initialize a later generation, copy a source marker, or assign the in-memory generation as the production path.

Reviewed installation must create an immutable execution-generation anchor in the fresh namespace. It binds owner, task, execution id, semantic id, parent lineage digest and generation. Both engine and generator must load this authenticated binding before constructing their control fence. Stop remains a separate one-way requested marker: its generation must follow the installed anchor, and a stale/lower marker or conflicting anchor fails closed. Initial legacy-generation1 behavior must remain unchanged for existing non-lineage sessions. A later-generation job must never fall back to1 because the anchor is missing.

Anchor initialization is still unimplemented. The proposed anchor is not permission to install it; only the reviewed installation transaction/journal can provide that authority. Tests that directly set an in-memory fence generation simulate the bound runtime and do not establish a production initialization method.

## Cloud ConOps boundary

Cloud Gate/Edit staging requires a current-version typed founder-confirmed ConOps document matching the immutable semantic source id. This preserves existing cloud producer preflight and handoff behavior. Generator CLI support for absent ConOps is not an optional-absence policy for cloud staging. Missing, null, stale-version, foreign-session or unconfirmed ConOps fails closed; no fabricated null file, restamping or implicit healing. Absent-ConOps cloud support remains a separate founder product decision, not implemented here.
