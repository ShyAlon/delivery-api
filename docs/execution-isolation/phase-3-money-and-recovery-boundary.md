# Phase 3: parent provider broker and exact compute evidence

2026-10-27. Additive private constructor seams only. No runtime selector,
activation, live DB schema/data change, deploy, restart or live model call.
Retail child refusal is deliberately STILL unchanged. Tests prove individual
boundaries; full app cutover/crash/resume acceptance is phase 4.

## Parent owns provider effects

Child sends a stable logical call ID, stage, reviewed provider/model and exact
SDK JSON bytes. No destination, headers, credentials, prices, settle, release,
refund or reconnect API crosses that channel. Parent validates strict fields,
model/cap, bounded bytes and live authority, records the logical dispatch,
reserves and binds an attempt, rechecks authority, starts, and sends exact bytes
to the fixed reviewed endpoint. Child SDK URL/headers are ignored.

The parent-only broker authorizer checks original owner/Auth session, current
worker job state/generation/model, active engine lease, Stop and installed
anchor evidence. Child supplies none of the trusted store/gateway/endpoint/
header/authorizer wiring. The won engine lease epoch comes from trusted launch
wiring, not from a child call. Backend RLS continues to check live Auth claims
per transaction. No new service-role access.

Parent buffers JSON or terminal-usage SSE within an 8 MiB bound, hashes and
stages exact response bytes + raw usage BEFORE settlement or child delivery.
Only status/content-type/body/hash are returned, no cookies or private headers.
Parent uses the existing gateway's raw-usage normalization/ledger settlement;
child cannot claim usage. Bad usage, non-200, missing terminal SSE or exceeded
bounds hold the attempt. No optimistic zero or auto-connect retry here.

Existing staged_provider_responses table is reused in a separate
`child:<operation_id>` namespace. Insert-before-admission, immutable attempt
binding and completed response remain enforced. A racing insert fails closed.
Parent death before/after start leaves an incomplete record: pause, never
redispatch. Completed response plus non-uncertain ledger state can settle/replay
without another provider call. Uncertain state needs explicit reconciliation.
Conflicting request or response hash refuses. A raw-result durability failure
holds instead of returning a usable unrecorded result. No DDL/migration.

## Child transport and adapters

Neutral package 0.2.0 is built once and pinned identically in delivery, engine
and spec-generator. BrokerClient only talks over its inherited FD; it has no
HTTP network transport, key or provider endpoint. Sync/async SDK transports
return buffered exact bytes, including completed SSE. A channel loss poisons
the client permanently, no retry/reconnect. HTTP clients disable ambient
proxies/alternate mounts. Provider waits are excluded on the compute channel.

Explicit `broker_clients` scopes in both consumers are separate from existing
local `wire_clients`. The old path is unchanged. Models are registered for that
scope/stage/provider/model; engine's lazy model cache rebuilds for every broker
scope. A stale model cannot retain another child's broker. Stable logical call
IDs MUST come from the durable graph/operation cursor, not random per retry;
this constructor requires the allocator but does not wire the app cursor yet.
The production start/answer/generator path remains unselected until phase 4.

## Compute

Separate inherited descriptor carries child monotonic start, wait.begin,
wait.end and finish, with strict increasing sequence and named wait identity.
Parent sums elapsed minus the UNION of provider/founder waits. Nested waits
never double-subtract. Delivery times/IPC latency never substitute for child
clock readings. Gaps, clock regressions, duplicate/unmatched waits, open waits,
missing finish or changed durable transcript refuse a price.

Parent opens an existing retail segment keyed by job+operation. Complete exact
transcript is durable in the existing staging table before finish_retail_compute.
The ledger's segment/contribution idempotency prevents a second charge. Failed
or missing measurements use existing close_failed rules: provably no provider
work can void, otherwise hold unresolved. No crash wall-time estimate. Finish
or settlement ack loss has durable evidence, not permission to create a new
operation/segment. Explicit recovery must compare the old transcript and ledger.
The accumulator's finish settles only compute evidence, NOT job completion or
provider quiescence. Reaping never cancels a remote call.

## Existing publication recovery stays authoritative

SameHostProducerWorker already hashes produced artifacts and stores a durable
produced marker before publication. Publication-only recovery reuses verified
bytes; paid evidence without marker pauses rather than reruns. These paths are
not replaced here. Phase-4 child coordinator must call the same ownership rules,
not add a second recovery owner. Missing/conflicting raw response, compute or
artifact evidence always pauses. No automatic fallback to an in-process rerun.

## Evidence and limitations

Hermetic fixtures cover exact-byte dispatch, request/model/foreign-field
refusal, stale/stop authority, incomplete/uncertain no-retry, raw durability
failure, settlement-ack replay, response hash mismatch, terminal SSE, private
socket loss and both explicit consumer hooks. A real fresh child exercises
broker+compute descriptors with fake parent HTTP and no key/DB environment.
Disposable real ledger tests settle provider and exact compute once/replay,
with original owner/Auth claims; no live DB/provider is contacted.

The shared schema source, wheel bytes and pins are checked. Clean install/build
routes must carry 0.2.0. Hosted CI and Docker execution remain unverified.
Existing opt-in engine checkpoint-claim test hidden red remains a separate debt
item recorded in phase 2; it is not concealed or fixed in this phase.

Not claimed here: complete app cursor allocation, all graph execution adapters,
concurrent total-pool measurements, OS-level isolation against hostile code,
production key provisioning or complete crash/stop/resume/gate acceptance.
Those are phase-4 cutover prerequisites. Retail refusal stays in place even
though component-level monotonic metering tests pass.

## Next and stop

Stop after the three phase-3 PRs for Shy's merge. Phase 4 connects typed
start/answer/generator operations to the supervisor and parent broker, durable
cursor/answer refs, claims/fences, produced/publication recovery, reviewed pool
budget 8+4 and compatibility-service accounting. It must remain opt-in and
run the full local crash/stop/resume/accounting matrix before paid-child refusal
can be considered for removal. No paid call or activation is authorized here.

## Additional combined-suite finding

Existing dual-provider-ledger fixtures leave two retail contribution rows, so
running that file before retail-ledger trips its zero-row assertion. Confirmed
identically on clean delivery main e2deeb5. Each bounded suite passes alone;
the combined failure is reported, not treated as green. Dated debt lesson
records the teardown lead and separate fix. New broker fixtures clean their
own rows and pass together with the retail suite. No assertion was weakened.
