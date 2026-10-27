# Phase 2: supervised child and published restore seam

2026-10-27. Constructor-only opt-in. No production CLI, HTTP route, env flag
or cloud_runtime change selects it. No new DB schema or pool cap changes.
The in-process path and exact retail-subprocess metering refusal remain.

## One schema, not two copies

Source of truth is `packages/requi-child-contract/src/requi_child_contract` in
delivery-api. A deterministic offline wheel builder produces version 0.1.0;
both repos vendor the identical wheel and lock it. Delivery's old module is a
compatibility import only. Engine imports the neutral package, never delivery.
Full image builds compare wheel bytes before installing. Existing install,
CI, dist and Docker test-context routes carry the wheel explicitly; no private
package fetch from a public index. Future schema edits require a new version,
rebuild, identical wheels and compatible pins in both repos. Do not edit a
vendored wheel or its installed source directly.

The wheel adds bounded lifecycle events: ready -> running -> completed,
stopped or failed, strictly increasing sequence and exact operation ID. It
contains no raw exception, arbitrary progress body or private artifact result.
Rich progress can continue through durable journals when cutover is wired.

## Supervisor

`ChildSupervisor` requires a trusted fixed executable/argv and an authorization
callback before spawn. A request cannot choose a command. One slot remains held
until the process is reaped. Every operation gets a new private socketpair and
process group. Credentials, provider keys, DB URLs, proxies and Python path
are not inherited. The caller must deliberately install any necessary private
FDs/config in later wiring. This is a same-user process boundary, NOT an OS
sandbox against hostile code with filesystem access.

Input is one versioned operation; output is strict sequenced lifecycle frames.
Frame limits, deadline, EOF, foreign/duplicate/trailing events and exit status
all fail closed. stdout/stderr go to DEVNULL: this seam cannot accidentally
leak a prompt or credential. Durable sanitized diagnostics are later wiring.

Stop calls the supplied durable fence callback before the cooperative STOP
frame. Grace defaults 10 seconds, TERM wait 5 seconds, then group KILL and reap.
Cancellation/protocol failure also group-cleans and reaps. Killing a process
never proves a remote provider call cancelled: ChildResult explicitly reports
provider_reconciled=False. It does not mark a job quiescent, settle/release
money, retry a job or publish artifacts. No fallback into the old thread path.

## Engine claim/restore seam

`restored_child_step` accepts only published advance/answer restore in this
phase. Start request loading and actual answer/generator dispatch are NOT wired.
Only an explicit isolated factory/action can exercise it; the production
factory is refused with the pending-money-boundary reason.

Trusted binding is supplied independently from the received frame. Store claims
must exactly match the owner and ORIGINAL AUTH SESSION, not execution session.
On a transaction-local claimed connection, lock the owner session row, compare
task/generation/expected pre-claim epoch and expiry, reject stopped control,
compare sequence and digest of published control+pointer, then claim epoch+1.
A live parent's lease is never reused. New child restores only that publication,
starts its own heartbeat, and releases on success or failure. Provider access
is not part of restore. Existing FORCE-RLS/live-session policies remain the
source of auth validity on each SQL operation; no new service-role path.

The v1 binding epoch is the EXPECTED PRE-CLAIM EPOCH. It is not permission to
reuse that epoch for new writes. Worker jobs have an attempt counter, not this
engine lease epoch; do not substitute one for the other. The trusted parent
must independently authorize job/attempt, reviewed model and installed anchor
before launch. The constructor seam does not yet perform that application
wiring and makes no claim otherwise.

Answer restore checks wait ID and the existing review revision. It does not
load answer_ref, commit an answer, implement operation idempotency or advance
an interview. Those belong in cutover after money/recovery gates. No paid run
may use this seam before those checks and durable dispatch are installed.

`serve_restored` reads the private descriptor, verifies the independent binding,
restores, runs an isolated action and emits terminal only after lease release.
Parent EOF/reset requests stop; a cooperative action sees it. An uncooperative
action requires supervisor escalation. Parent hard-crash means connection
loss and lease expiry fence later DB writes. No remote-provider-cancel claim.

## Acceptance in this phase

Real local process fixtures cover lifecycle, secret-free env, malformed/oversize
frames, foreign IDs, duplicates, crashes, timeout, cancellation, durable stop
ordering, stubborn child and descendant group cleanup, slot lifetime.
Disposable PG fixtures cover published restore across a real fresh process,
Auth-session claim separation, stale generation/epoch/digest/sequence/task,
live competing lease, stopped session, changed wait/review, parent loss, release
on errors and exact shared-wheel bytes. Full paid crash/settlement, answer
dedup, generator adapters and bounded total pool-count matrices are NOT claimed
here. No live provider or user DB is used in these fixtures.

## Next phase and stop

Phase 3 touches delivery-owned provider dispatch/broker, exact request/model
identity, staged response replay, cross-process monotonic compute/wait events,
uncertain dispatch/crash reconciliation and publication-only recovery. Keep
retail refusal until exact metering/recovery tests prove safe. It may require
spec-generator's existing FD adapter; report that before expanding repository
scope. Actual app cutover, pool budget 8+4, compatibility 8010 retirement and
production entrypoint wiring remain phase 4+. Stop after these two additive
phase-2 PRs for Shy's merge. No deployment, restart, spending or live DB change.

## Validation caveat

Enabling engine's optional `test_pg_claim_transport.py` against the sibling
delivery migrations gives two passing cases and one hidden red at the checkpoint
saver case's engine_sessions RLS insert. It fails identically on a clean archive
of engine main. The case rebuilds a raw pool from conn.info.dsn and drops the
explicit transaction claims. Engine's dated debt lesson records reproduction,
causal lead and separate-item scope. Assertions are retained, no RLS changed.
The NEW child compare-and-claim method passes a separate real-migration
app-role test, including nonexistent/revoked Auth session and foreign owner.
