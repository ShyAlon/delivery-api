# Child execution: phase 1 contract and baseline

2026-10-27. Proposed wire v1, inert foundation only. Delivery baseline
37606f54a95e1969e511cf5d307a179440ac8a5a; engine baseline
0d1d48ad8f2f8bd40668112494d5c14ba72865c0.

## What ships and what does not

`child_execution_contract.py` validates an immutable, versioned request envelope;
`tests/fixtures/child-execution-v1-answer.json` is the cross-repo wire fixture.
There is no runtime consumer, launch, feature switch, recovery loop, provider
call, pool change or DB migration. Current in-process defaults are unchanged.
A static regression pins that no execution module imports this contract yet.
The existing paid-subprocess refusal is exercised directly and remains intact.

This is NOT a capability or an authorization proof. The expected job binding
must come from verified durable state, never a child-supplied envelope. Future
consumers must revalidate current owner auth, job state, generation, lease,
stop fence, reviewed model, installed anchor and operation evidence at each
write/admission boundary. `require_binding` is equality only, not that check.

## Request wire v1

One UTF-8 JSON object, newline terminated, at most 64 KiB including newline.
Reject duplicate keys at any depth, multiple frames, unsupported version,
non-finite constants, extra keys and scalar coercion. No executable, shell,
callback, filesystem path, credential, price or arbitrary URL is allowed.

- Envelope: integer version 1; operation_id; job binding; discriminated operation.
- Binding: canonical owner and Auth-session UUIDs, task/session/job IDs,
  generation, lease_epoch, reviewed provider/model, and installed execution
  anchor digest when generation > 1. Auth-session and execution-session IDs
  are distinct fields. A digest still needs live installed-anchor verification.
- `interview.start`: durable request_ref.
- `interview.advance`: published checkpoint digest + expected sequence.
- `interview.answer`: same checkpoint/sequence plus wait_id, review_revision,
  durable answer_ref. Never pass an in-memory callable or unrecorded answer.
- `generator.phase`: allowlisted phase + input-manifest digest.

Refs are bounded opaque identifiers, not paths. Versioning is explicit:
unknown versions/kinds fail closed. Operation IDs are required but idempotency
is NOT implemented by this codec: receiver persistence/dedup is phase 2+ work.
No success/recovery promise should be inferred from parsing a frame.

## Baseline and future consumer inventory

| Concern | Current owner / source | Later boundary work |
| --- | --- | --- |
| Runtime installation | delivery `cloud_runtime.py:install` builds gateways and inprocess hosts | Supervisor adapter, default unchanged until reviewed cutover |
| Generator launch | `inprocess_engine.py:dispatch`, VirtualProcess + SameHostProducerWorker | Fresh child/process group, typed lifecycle and reap |
| Interview step | `inprocess_interview.py:run_step` captures a Python call closure | Engine typed entrypoint restores published state; no closure transfer |
| Worker authority | `tenant_jobs.execute_job`, WorkerCapability.capture/validate | Parent claim and receiver live evidence, stale generation rejection |
| Provider money | ProductionGateways + WorkerAdmissionGateway | Delivery-owned exact-byte broker; retain uncertainty/replay rules |
| Existing descriptor protocol | `admission_transport.py`, `admission_handoff.py`; engine counterpart | Reuse bounded private channel; do not add public listener |
| Compute | SameHostProducerWorker + ComputeMeter, wait-union exclusions | Cross-process sequenced boundaries; no guessed crash charge |
| Paid subprocess guard | `producer_admission.current_worker_gateway` | Refuses retail until exact compute is implemented and tested |
| Artifact publication | producer_results hashes + publish_session_revision | Reuse verified bytes; crash after production is publication-only |
| Gate/answer control | existing DB review revisions + typed engine API | Parent persists answer before operation; repeated ID never advances twice |
| Stop/quiescence | durable generation fence + filesystem marker + worker verifier | Persist stop, cooperative request, process group kill/reap, reconcile provider |
| DB claims/leases | delivery postgres_scope; engine pg_claims/pg_session_store | Claims per transaction; execution session must not replace Auth session |

### Connection consumers

Delivery postgres_scope and in-process engine currently share `db_pool` max 10.
Operator reads borrow that same DB abstraction. Engine standalone `pg_pool`
max is 4, but its in-process injected pool overrides that standalone cap.
`full_entry.py` also launches the compatibility 8010 engine service: a child
mode must count or remove that consumer, not silently add another pool.
Tests prove current ownership; this PR does not change the caps.

Proposed future total remains delivery 8 + one active child 4 = 12, leaving
three of the observed 15 pooler slots for non-application use. Before enabling
child mode, inventory actual eager/lazy consumers and reject oversubscribed
configuration. Queued/parked tasks must not create pools or processes. A new
concurrency policy is not part of this foundation.

### Repository impact

Delivery owns this request schema initially. Phase 2 must establish one schema
source consumed by both sides before enabling any receiver; do not copy models
into engine. Engine already has an admission FD client/handoff but lacks the
new typed restore-and-step entrypoint. Spec-generator already has an inherited
FD gateway and worker runtime, so no spec-generator change is justified in
phase 1. A later adapter need remains possible and must be reported explicitly.
Do not add a shared-package release or third repository merely for this inert
proposal; choose distribution at the first real consumer integration.

## Crash/recovery acceptance matrix for later consumers

| Cut point | Required outcome, not implemented by codec |
| --- | --- |
| Before job claim or invalid binding | No child, no provider reserve, no state write |
| After claim, before dispatch | Fence/reap; proven-undispatched release only |
| During provider call / lost descriptor | Uncertain attempt, no automatic retry or refund |
| Raw response recorded, before settlement | Replay exact durable response, settle once |
| Settled, before produced marker | Pause for evidence, never rerun paid work blind |
| Produced marker, before publication | Hash-verified publication-only retry |
| Stop with provider work active | Reap alone does not prove remote request cancelled |
| Parent dies / stale child survives | No new dispatch; current generation fences late publication |
| Repeated answer/operation ID | Same recorded result or refusal, no second advance |

Phase 1 supplies malformed/foreign/stale-binding fixtures and this baseline
matrix, not false crash acceptance. Real spawn/crash fixtures belong with the
supervisor in phase 2; money-boundary crash tests gate phase 3.

## Next phase and stop point

Phase 2 is opt-in supervised child seam with typed lifecycle/progress, explicit
DB restore/claims, stop and reap, fake provider fixtures only. It touches
delivery supervisor/runtime selection and engine restore/operation dispatch;
may need a shared schema distribution decision. It must leave current default
in-process, preserve paid refusal and keep both version pins compatible.
No phase-2 code starts before phase 1 is reviewed/merged and main authorizes
the continuation. No activation, spending, live DB change, deploy or restart.
