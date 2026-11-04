# Theme 3: test once, move the same image

Status: design, not a runnable handoff or deployment approval. CI unavailable,
local gate only. No Docker executable is available in the implementation
workspace; actual image build/run must pass on a Docker host before readiness.

## Evidence for the change

`build-local-image.sh` defaults to the Mac's architecture. `build-full-image.sh`
archives the sources again and builds on the server. `deploy-full.sh` chooses a
source tag and can trigger that remote build. The same source revisions therefore
do not imply the same dependencies, architecture, layers or runnable bytes.
Preview has a second launch specification, including different volumes and
startup gates. The owner approved identical execution locally/remotely and a
local test of the image subsequently deployed.

## Required invariant

Build one linux/amd64 service image locally, including on Apple Silicon through
emulation. Test that immutable local image ID. Export it once. Transfer and load
the resulting archive without rebuilding. Before replacing any service, verify
that the loaded image's config digest, OS/architecture and source tuple match the
acceptance receipt. Start by immutable ID, never by the mutable tag.

A Docker image ID is a config-object SHA256 digest. It is not a registry manifest
digest. In this archive-based workflow the receipt binds both that image ID and
the SHA256 of the exported archive. A future registry route must separately bind
and test the single-platform manifest digest; no ambiguous `digest` field.

## Receipt and gate model

Use a versioned JSON receipt with full four-repository SHAs, target platform,
image config digest, runtime contract version/hash, archive SHA256/size,
acceptance version/hash, start/end times and named successful checks. No DSNs,
provider keys, signed cookies, or environment values belong in it. Repository
local state is an operational record, not a security signature. Validate strict
schema, all hashes, image inspect results and an allowlisted receipt path. Do not
consume arbitrary shell or free-form commands from this file.

Write it atomically only after all checks pass and the archive is complete.
Failure leaves no accepted receipt. A source tag is a discovery alias only. A
missing or mismatched receipt refuses deploy instead of building remotely. A
changed acceptance/runtime contract invalidates prior acceptance. `--fast` can
reuse only the exact accepted image with matching contracts; it never accepts a
matching source tuple as proof.

## Exact-image acceptance, not a derivative claim

The existing PG-smoke Dockerfile adds packages and tests a derivative, not the
unchanged service image. Keep it as optional diagnostic coverage, but it cannot
produce the exact-image acceptance receipt.

The candidate service must itself run as UID10001, read-only, with the production
entrypoint and shared runtime options. Own a disposable PostgreSQL sidecar and
isolated Docker network; initialize only that owned database with the current
schema and a restricted app role. Supply fictional signed tenant fixtures and
fake provider responses through an explicit test harness, never through a
production behavior flag or host credentials. Verify actual startup, signed HTTP
reads, exact child phase execution/Stop/gate handoff and retained outputs. Prove
host browser reachability separately where required. Do not label anonymous
health or import smoke as lifecycle acceptance.

Test-only schema/client orchestration may run in a harness container. The
service image bytes and entrypoint stay unchanged. No package installs, source
mount overlays, dev dependency imports, live DSN, host data volume or paid
provider calls may supply acceptance. The disposable network needs explicit
outbound isolation plus the expected local fixture endpoints. Failure and
interrupt cleanup removes only positively identified invocation-owned resources.

## One launch specification

A single host-side runtime planner owns user, read-only filesystem, dropped
capabilities, no-new-privileges, PID/memory limits, temporary scratch, internal
port and command. Both local preview and remote deployment consume it. Target
values are image ID, container name, host bind port and settings file. Public
origin, database and provider endpoints/credentials remain values. No local,
remote, closed-cloud or attended behavior modes. Scratch replaces obsolete
persistent artifact volumes; no durability is claimed for it.

Keep Back office a private separate listener if that is the existing product
boundary. Do not silently expose it or change operator capabilities as part of
image transport.

## Owner-side workflow

1. `req image` reviews clean source revisions/platform and builds the candidate
   locally after the existing owner confirmation. No remote build.
2. Run exact-image acceptance against owned disposable services. Only then save
   the receipt and archive. Local preview launches that same accepted image ID
   with owner-selected real settings. Paid work still needs its own approval.
3. `req deploy` defaults to a local dry review of receipt/archive/launch values.
   Only an explicit owner apply may contact the named server. Verify target
   architecture, free space and settings readiness before stopping anything.
4. Transfer to an owned temporary remote file, verify archive checksum, load,
   inspect and verify image ID/platform. Retain the prior exact container and
   settings identity for rollback. No build, pull or source fetch on the server.
5. Start by ID with the shared spec. Verify readiness and actual running `.Image`
   equality. On failure restore the prior service and report the final state.
   No unrelated container cleanup, Caddy edits or data migration piggyback here.

The current assignment permits writing and testing this workflow, not executing
its remote or paid steps. The owner is the merger and deployment decision-maker.

## Contract tests before Docker-host gate

Fake-command tests must cover tag replacement after acceptance, wrong loaded ID,
wrong architecture, changed archive, truncated receipt, unsuccessful smoke,
interrupted export/receipt write, stale source tuple, contract drift, deploy
without receipt, no SSH before apply, no remote rebuild/pull, no stop before
verification, failed readiness rollback, and identical local/remote runtime
options. Verify shell quoting with paths containing spaces. Fake-command tests
are not a substitute for a real amd64 build/run on a Mac Docker host.

## Delivery sequence

One WIP branch, pushed after meaningful units. Rebase only after the parent
confirms DB-only PR672 merged, then run the complete applicable gate before the
single theme PR. Keep the four inherited UI failures intact and outside this
branch. Their small UI PR is queued afterwards. No automatic schema apply, live
writes, paid execution, deployment or merge is authorized.
