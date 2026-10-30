# Integrated image test preparation

This is a test image, not the production launcher. It installs the exact built
contracts, delivery, engine and generator wheels and runs fake SDK Stop checks.
It does not connect a provider, mount existing accounts, apply migrations, enable
cloud execution, launch the UI or exercise the full production lifecycle.

Requirements: Docker Desktop/Engine, Python 3, uv and the four local source repos.
The preparer refuses tracked source edits and records full source SHAs and wheel
hashes. Commit the intended test snapshot first. It does not fetch or push.

From delivery-api, with matching source snapshots:

```bash
python3 scripts/docker/prepare-context.py \
  --delivery "$PWD" \
  --engine ../interview-engine \
  --generator ../spec-generator \
  --contracts ../vmodel-contracts \
  --constraints scripts/docker/tested-constraints.txt \
  --output /tmp/requi-re-image-test
scripts/docker/run-test-image.sh /tmp/requi-re-image-test
```

The wheel build uses the local uv build cache. The image build needs access to
Python's image and dependency downloads. Runtime has no network, host mounts,
provider secrets or exposed ports. Its root is read-only; scratch is temporary.
Success prints a JSON result followed by the image tag. Build or runtime failure
is not a production readiness result. Preserve the source manifest and full
build/run output for diagnosis.

Validation status: local exact-wheel build, installed-wheel invoke/stream Stop
smoke and packaging contracts passed. Docker image build/run has NOT been run in
the preparation environment because Docker is unavailable. The image is not yet
certified as a ready Mac handoff. Both arm64 and amd64 container execution remain
to be checked. No claim is made that these dependency pins have been resolved on
both target platforms; the real image build is that check.

## HTTP + browser test variant

Add `--ui-test` to prepare-context.py to select the installed-wheel Chromium
image instead of Stop-only smoke. Use the same run-test-image.sh command. It
starts delivery web and private back-office listeners inside the isolated
container and checks actual browser auto-loading, expanded detail API reads,
per-task token/credit rendering, JSON download, mobile layout and revocation.
No host port is exposed. This proves browser-to-listener reachability inside the
container, not host-browser reachability or production authentication/RLS.

The fixture injects a fake verified identity and in-memory read adapter only in
the test script. No production registry is enabled, provider is called or SQL
migration applied. Browser requests outside the two loopback listeners are
blocked. Build downloads Chromium and its OS dependencies; runtime network
remains disabled. Screenshots live only in temporary container scratch.

The UI variant still needs actual Docker arm64/amd64 build/run validation.
A passing Python/Chromium run outside Docker does not establish that result.

## Mac host-browser fake preview (candidate, Docker validation pending)

For a UI-test context, run `bash run-host-preview.sh /path/to/context`.
It builds the UI image, runs the internal automated browser gate, then starts a
separate fake read-only preview on loopback-published web8000/back-office8020.
It checks both host health URLs before printing `/preview` links. Those links
set a fake fixture cookie, not a real sign-in. Open the back office to expand
fake session details, usage and state events. Press Ctrl-C to clean up.
PREVIEW_WEB_PORT and PREVIEW_BACKOFFICE_PORT may select unused ports1024..65535.
No host database, environment file, secrets or provider access is passed in.

Preview uses an internal Docker bridge plus an application non-loopback outbound
socket deny guard. Internal bridges still permit gateway/host communication;
this is not equivalent to network-none. Only loopback host ports are published,
with Docker Engine28+ required because older engines can expose localhost ports
to the local network. Docker Desktop port publication on an internal bridge
still needs actual Mac validation; a failed host health check is failure, not
readiness. No fallback to a public bind or ordinary internet-enabled bridge.

Network references:
https://docs.docker.com/reference/cli/docker/network/create/
https://docs.docker.com/engine/network/port-publishing/

## HTTP + browser test variant

Add `--ui-test` to prepare-context.py to select the installed-wheel Chromium
image instead of Stop-only smoke. Use the same run-test-image.sh command. It
starts delivery web and private back-office listeners inside the isolated
container and checks actual browser auto-loading, expanded detail API reads,
per-task token/credit rendering, JSON download, mobile layout and revocation.
No host port is exposed. This proves browser-to-listener reachability inside the
container, not host-browser reachability or production authentication/RLS.

The fixture injects a fake verified identity and in-memory read adapter only in
the test script. No production registry is enabled, provider is called or SQL
migration applied. Browser requests outside the two loopback listeners are
blocked. Build downloads Chromium and its OS dependencies; runtime network
remains disabled. Screenshots live only in temporary container scratch.

The UI variant still needs actual Docker arm64/amd64 build/run validation.
A passing Python/Chromium run outside Docker does not establish that result.

## Mac host-browser fake preview (candidate, Docker validation pending)

For a UI-test context, run `bash run-host-preview.sh /path/to/context`.
It builds the UI image, runs the internal automated browser gate, then starts a
separate fake read-only preview on loopback-published web8000/back-office8020.
It checks both host health URLs before printing `/preview` links. Those links
set a fake fixture cookie, not a real sign-in. Open the back office to expand
fake session details, usage and state events. Press Ctrl-C to clean up.
PREVIEW_WEB_PORT and PREVIEW_BACKOFFICE_PORT may select unused ports1024..65535.
No host database, environment file, secrets or provider access is passed in.

Preview uses an internal Docker bridge plus an application non-loopback outbound
socket deny guard. Internal bridges still permit gateway/host communication;
this is not equivalent to network-none. Only loopback host ports are published,
with Docker Engine28+ required because older engines can expose localhost ports
to the local network. Docker Desktop port publication on an internal bridge
still needs actual Mac validation; a failed host health check is failure, not
readiness. No fallback to a public bind or ordinary internet-enabled bridge.

Network references:
https://docs.docker.com/reference/cli/docker/network/create/
https://docs.docker.com/engine/network/port-publishing/

## Fresh dependency check before another handoff

From delivery-api, use Python3.12 with the final extracted UI-test context:

```bash
python scripts/docker/verify-clean-install.py /path/to/extracted/context
```

This creates a new temporary venv with no system-site packages, verifies the four
wheel names/content/hashes against the context manifest and Docker hash file,
installs only the packaged requirements plus Docker's explicit Playwright pin,
checks dependencies and runs the installed UI smoke. It does not use dev extras,
source PYTHONPATH, account environment variables or existing Python packages.
An explicit --browser-path may reuse Chromium binaries only; otherwise it
installs Chromium in temporary scratch. OS browser libraries must already exist.
This is a dependency-completeness check, not Docker/arm64/read-only-root or
host-browser bridge certification. Those checks remain separate.

## Next-package build caching
The host launcher derives a SHA256 image tag from sorted context file names and bytes (not modification times) and reuses that local image when present. The automated network-none/read-only browser gate still runs on every launch before the interactive fake preview. Symlink contexts are rejected. Removing that image forces a rebuild. This keys the supplied context, not mutable upstream image/index freshness.

The UI Dockerfile installs pinned Playwright/Chromium first, then separately copies, verifies and installs contracts, engine, generator and delivery wheels in dependency order. Each repo has its own checksum file. Aggregate wheel-sha256.txt remains unchanged in shape and is checked again after all installs, followed by pip check. Constraints apply to every install. A changed repo wheel invalidates its layer and those below it; changing constraints or the base image invalidates earlier layers too. These are cache design guarantees, not a measured speedup. Actual Docker build timing still needs a host run.

Next UI contexts include the isolated private acceptance HTML/renderer and fictional typed source fixture. Network-none automated image gate runs the existing HTTP smoke then acceptance-smoke.py. The latter imports the built-wheel presenter, renders its exact digest-bound fixture locally, checks stale-review rejection and retained guidance/mobile width. The interactive host app still does not expose this private page. Clean verifier runs both when the acceptance smoke is included. This tests presentation only, not authenticated owner acceptance or installation. No Docker execution is established by preparation.

### PG-only smoke setup

No service image supplies a database default. For installed-wheel delivery-only
smoke, run the supervisor with a Python that has pgserver and psycopg:

```
python scripts/docker/owned-pg-smoke.py "$PWD" /path/to/clean-venv/bin/python scripts/docker/control-plane-smoke.py
```

It rejects inherited database settings, starts its own disposable server, applies
this checkout's exact migrations, and gives the smoke a restricted app-login DSN.
The service command remains the installed-wheel interpreter, not this checkout.
For the network-none container gate, `Dockerfile.pg-smoke` is a test-only derivative
with the same supervisor, harness, and migrations, plus Debian-native PostgreSQL. Never publish that derivative
as the runtime service image. Actual Docker build/run remains a separate gate.
Full-image smoke similarly requires a supervisor-owned DELIVERY_TEST_POSTGRES_URL;
there is no file persistence opt-in or implicit live database.

`ops/latency_compare.py` was retired: it compared only the removed store with its
old replica. Use `testing/measure-pg-slice.py` for current PG contract/query slices.

For a full-service candidate, build that same derivative with
`--build-arg SERVICE_IMAGE=YOUR_LOCAL_FULL_IMAGE` and run it with this override:

```
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 \
  YOUR_LOCAL_TEST_DERIVATIVE python /smoke/owned-pg-smoke.py /smoke python /app/full-smoke.py
```

This keeps the candidate's actual entry point and installed wheels while adding
only test dependencies/schema to the derivative. No deployment is performed.

### Build and reuse gate (2026-10-30)

Every full-service caller now uses `smoke-local-image.sh IMAGE [PLATFORM]`.
It builds the test-only derivative from the exact candidate image, adding Debian-native
PostgreSQL, the owned-PG supervisor and this checkout's migrations. It passes no host settings,
DSN, env file, provider key or mounted data to the smoke run. The run is non-root,
read-only, network-none, with only a private 512 MB `/tmp` for the disposable PG
and runtime scratch. The PG server listens on a Unix socket only. The temporary
derivative tag is removed afterward, including on failure. Service images are
unchanged and only published by the existing callers after smoke success.

`smoke-image.sh` stages only those public harness/schema files over SSH and uses
the same helper with sudo Docker. This also covers server build, server reuse,
deploy preflight and the remote preview path. Building the test derivative may
need package-download access, just like building the service image; its actual
smoke run has no external network. No live database is used to prove an image.
