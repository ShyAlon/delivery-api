# Owner exact-image workflow

Draft, not yet Docker-host validated. CI unavailable, local gate only.
Do not deploy from this document. The actual amd64 build/lifecycle gate must
pass on the Mac before this image is accepted. No paid task is started here.

## Before merge: test the PR checkout

`req` deliberately syncs clean main, so it cannot test this unmerged branch.
Use the repo-owned entry directly from the reviewed PR checkout instead:

```bash
bash scripts/ops/image.sh --review
bash scripts/ops/image.sh --accept-local
```

Keep delivery-api, interview-engine, spec-generator and verification-loop as
sibling clean tracked checkouts. Existing untracked settings are not archived.
The delivery checkout needs its `.venv` dependencies. Docker Desktop must be
running with linux/amd64 build support, including emulation on Apple Silicon.
The build downloads base images/dependencies. Acceptance uses only fictional
identity, owned disposable PostgreSQL and a zero-cost local provider fixture.
It checks signed HTTP, actual child Start/answer, active-call Stop and retained
PG evidence. No existing DSN or provider key supplies the acceptance fixture.

The script prints progress and asks for literal `yes`. It keeps private keyed
state under `~/.local/share/requi/image-workflow`. Repeat the same command to
revalidate/reuse the exact built image; acceptance runs again, not a saved flag.
If it fails, do not publish or deploy. Share the fixed failure message, not
settings, Docker login files or tokens. Docker-host failure diagnostics may need
follow-up; no automatic alternate image or remote build is allowed.

## After merge: same entry via req

`req image --review` and `req image --accept-local` use clean current main.
Local preview is separate: `req preview --apply` requires existing private
settings at `~/.config/requi/preview.env` and the exact accepted local image.
It does not automatically stop an existing preview, migrate schema, approve a
route or start a task. Health is not signed route admission. The private Back
office remains a separate loopback listener. Ctrl-C closes only that listener;
the service container remains for inspection.

Publication is a later explicit owner action:

```bash
req image --publish ghcr.io/OWNER/PACKAGE
```

Replace OWNER/PACKAGE with the existing PRIVATE GHCR package. Existing local
Docker and GitHub CLI package credentials are required. This command reruns
local acceptance before pushing that same image. It refuses unverified or
public package visibility. A manifest SHA256 and tested config SHA256 are bound
in `accepted-image.json`. An OCI index or changed config refuses acceptance.

Deployment review is a separate later action, not part of the Mac gate:

```bash
req image --deploy ghcr.io/OWNER/PACKAGE --host USER@HOST
```

The review does not contact the server. It prints the exact command for obtaining
old container/image IDs during first adoption. The owner must review those IDs,
target name/port/settings, then separately choose `--apply`, supplying the exact
previous container/image pair if the old container has no workflow label. A
read-only registry token is entered at the hidden local prompt, never in chat.
No server build, source fetch, proxy edit, schema apply or unrelated cleanup.
Failed candidate startup requests rollback; a restarted prior container is not
proof that its health passed. Inspect final state before retrying.
