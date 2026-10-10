# Requi.re

**From a real conversation to a spec you can prove.**

`delivery-api` is the web and job-control repository for **Requi.re**, plus its reactive UI. The sibling `interview-engine`, `spec-generator`, `verification-loop`, `vmodel-contracts`, and `vmodel-spec-app` repositories remain separately named implementation components of the Requi.re product. The server owns process lifetime, durable task/phase/event state, SSE progress, artifacts and rerun generations. It does not reimplement interview or specification logic.

## Run locally

The supported local entry point installs or refreshes the editable environment when needed, enables local email/password accounts, selects the engine interpreter, uses one Ollama request at a time, and opens the folder-based session workspace:

```bash
cd ~/development/require/delivery-api
scripts/run-ui.sh
```

Open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/docs. The local model route defaults to `ollama:qwen2.5-coder:14b` (the standing interview-engine default); set `DELIVERY_OLLAMA_MODEL=phi4` before `scripts/run-ui.sh` to run the local route on another Ollama model. `OLLAMA_NUM_PARALLEL=1` prevents overlapping local requests. Existing completed sessions load from the artifacts folder configured in the UI, not from a file picker.

The UI has a V-shaped process map per task and distinct not-started, running, waiting-for-answer, and completed views. Live events use SSE. Live output has a compact adaptive scrolling viewport so the answer composer remains visible. Visible bundle pages are human-readable; use the item actions to inspect source JSON on demand.

## Local persistence and the cloud seam

SQLite is the default, not an API assumption. Both editable development and built distributions use the same stable per-user database by default: `${XDG_DATA_HOME:-~/.local/share}/requi-re/delivery.db` (on macOS without `XDG_DATA_HOME`, `~/.local/share/requi-re/delivery.db`). The database is outside every checkout and distribution, so pulls and repeated builds never copy, replace, or delete accounts. Override it when needed:

```bash
export DELIVERY_DATABASE_URL="sqlite:////absolute/path/to/delivery.db"
```

Existing databases inside an older `dist/requi-re` folder are not migrated automatically. Shy is the sole current user and already recreated his account; leave that old build untouched for audit or remove it after confirming the stable database has the account.

All routes, background jobs and the subprocess runner depend on the `Storage` protocol. Only `SQLiteStore` contains SQL or a database connection, and `create_store(DATABASE_URL)` is the single backend selection point. A Postgres adapter can implement the same protocol and be selected there; the HTTP and CLI job layers do not change. Additive local schema migrations run when SQLite opens, preserving existing task/audit history. `DELIVERY_DB_PATH` remains accepted for older setups. Rerun policy is isolated in `Store.prepare_rerun`; archive is implemented as the reversible option, while wipe and overwrite remain blocked until the founder chooses.

## Delivery-only container (fenced)

The root Dockerfile installs only delivery-api and the bundled contracts wheel.
No interview-engine, spec-generator, Ollama or provider SDK is installed. It runs
as a non-root user with the delivery-only launcher: reads and health are available,
mutations/engine dispatch/payments remain disabled. It is not a working full-run
image or launch approval. Existing fake integrated test images are separate under
scripts/docker and still contain the engines for dry validation.

```bash
docker build -t requi-control-plane .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  requi-control-plane
```

Runtime depends on PostgreSQL/Supabase configuration for the tenant control plane;
the empty temporary SQLite default exists only for network-none packaging smoke.
Never point a production launcher at a shared SQLite/Turso fallback. No provider
credentials or existing user database are passed to the smoke run.

## Private deployment preparation

No deployment was performed by this change. Dry tests exercise a stub Docker
boundary; actual amd64/arm64 image build/run remains a separate required check.
Docker is unavailable in the preparation environment. Installed-wheel startup
without engine/provider packages is evidence of packaging, not container proof.

Manual CI can build immutable SHA-tag images; no mutable main tag is published.
The deployment job is off unless CONTROL_PLANE_DEPLOY_ENABLED=true is explicitly
set, and uses the build's immutable registry digest. Do not set that variable or
run remote deployment before the owner's step-6 scope review.

The prepared script requires an immutable image digest and an existing owner-only
reviewed env file (for CI: /etc/requi/control-plane.env). It validates PostgreSQL
and Supabase fields without printing values, starts an unpublished candidate,
checks health, then replaces the loopback listener. The exact previous container
and config are retained for rollback. A candidate failure leaves the old service
untouched; cutover failure restores it. Existing candidate/previous containers
block another deployment pending operator review. No image/data pruning occurs.

```bash
# Preparation syntax only, not permission to deploy:
scripts/deploy-server.sh registry/image@sha256:ACTUAL_DIGEST /path/to/reviewed.env
```

Runtime flags: read-only root, non-root UID, no capabilities/new privileges,
bounded pids and temporary scratch, loopback-only host publication. No engine or
billable worker runs in this profile. Host provisioning, TLS/auth, protected
credentials, migrations, live health and recovery remain rollout checks.

## Production authentication and per-user token quotas

Local mode remains frictionless: if `DELIVERY_SESSION_SECRET` and OAuth client IDs are unset, requests run as `local user` with no login wall. Never use local mode on an exposed server.

Production OAuth setup:

1. Choose the public HTTPS origin, for example `https://vmodel.example.com`. OAuth callbacks are `https://vmodel.example.com/auth/google/callback` and `https://vmodel.example.com/auth/github/callback`.
2. Google Cloud Console: create/select a project, open APIs & Services > OAuth consent screen, configure the app and authorized users/audience, then Credentials > Create credentials > OAuth client ID > Web application. Add the HTTPS origin and Google callback exactly. Copy the client ID and secret.
3. GitHub: Settings > Developer settings > OAuth Apps > New OAuth App. Set Homepage URL to the HTTPS origin and Authorization callback URL to the GitHub callback exactly. Register, then generate and copy a client secret.
4. Generate a session secret (`openssl rand -hex 32`) and set these only in the server environment, never in git:

   ```bash
   DELIVERY_SESSION_SECRET=...
   DELIVERY_PUBLIC_ORIGIN=https://vmodel.example.com
   DELIVERY_GOOGLE_CLIENT_ID=...
   DELIVERY_GOOGLE_CLIENT_SECRET=...
   DELIVERY_GITHUB_CLIENT_ID=...
   DELIVERY_GITHUB_CLIENT_SECRET=...
   DELIVERY_ADMIN_EMAILS=shy@example.com
   DELIVERY_COOKIE_SECURE=1
   DELIVERY_DEFAULT_USER_TOKEN_QUOTA=250000
   ```

5. Restart and verify `/api/auth` reports `mode: oauth`; sign in once with each provider and confirm logout clears the session. Cookies are opaque, HttpOnly, Secure, SameSite=Lax and expire after 12 hours by default. OAuth state is one-use and expires after ten minutes. Auth and run-start endpoints are rate-limited.

Live mutations (create/start/rerun/gate decisions) require login when OAuth is configured. Task/replay browsing remains public. Audit rows use the signed-in email/identity.

Token quotas are integer model tokens across input plus output. The usage ingest API accepts `input_tokens` and `output_tokens`, attributes them to the run owner, and a new run is rejected before launch once `tokens_used >= token_quota`. Every allow/reject decision is audited. Shy administers a user after their first login:

```bash
curl -X PUT https://vmodel.example.com/api/admin/users/google:PROVIDER_ID/quota \
  -H 'Content-Type: application/json' --cookie 'vmodel_session=YOUR_ADMIN_BROWSER_SESSION' \
  --data '{"token_quota":500000}'
```

Prefer the admin API from the authenticated web origin rather than copying cookies into a shell. `GET /api/admin/users/{user_id}/quota` reports the configured limit and consumed total. Only identities whose normalized email is in `DELIVERY_ADMIN_EMAILS` can use these endpoints.

## Credits

Price events and the audit report usage in **credits** (`tokens * PRICE_PER_TOKEN + deterministic ms / 10 * PRICE_PER_10MS`) and the customer USD price (`credits * USD_PER_CREDIT`, default 1.00). Set these in `.env`; an old PRICE_ value overrides the defaults. Events written before the rescale have no `rates.scale == "credits"` marker; the UI shows them as "legacy scale" and the audit totals exclude them.

## UI conventions

- Every view-level page header remains visible while its page scrolls. Use the shared `page-head` class, which sticks below the global navigation and preserves the current translucent surface treatment.
- Do not add a large promotional/marketing hero above a working collection. Put collection actions, filtering, and current source context in the sticky page header so the managed objects stay primary.

### Local accounts and workspace

`scripts/run-ui.sh` enables email/password accounts by default. Passwords require 12+ characters with uppercase, lowercase, number, and symbol, and are stored only as per-user salted scrypt hashes. Browser sessions use random opaque handles in HttpOnly, SameSite=Lax cookies; only their SHA-256 digests are stored. Set `DELIVERY_BASIC_AUTH_ENABLED=0` only when intentionally restoring the unauthenticated local development mode.

Accounts identify actions, while discovered artifact sessions remain one shared local workspace. This fits the single-machine founder-demo workflow and avoids duplicating or moving the source artifact tree per account.

Email addresses are normalized to lowercase and must have a local part, `@`, and a dotted domain. Older pre-email identifiers remain in the local database for audit continuity, but must be recreated with an email address to use the current sign-in UI; OAuth accounts are unaffected.


### Engine interpreter and logs

Development mode starts `interview-engine` from its sibling folder. Interpreter precedence is: `INTERVIEW_ENGINE_PYTHON`, `VMODEL_PYTHON`, the interpreter saved through **Artifacts folder > Engine Python**, an engine virtual environment, the shared checkout virtual environment, then the active `VIRTUAL_ENV`. The UI validates candidates before saving one. A built distribution does not scan: both the server and engine always use `dist/requi-re/runtime/bin/python`.

Requi.re writes structured, timestamped activity to the console and `run.log`, including navigation, authentication, engine preflight/output, task actions, and replica sync boundaries. User-visible failures stay in `Error / Likely cause / Next` form; raw Python and native-build diagnostics belong in `run.log` and `dist/requi-re-build.log`. Secrets such as Turso tokens are never logged.

## Development vs build/deploy

Development keeps the sibling repositories editable and uses `scripts/run-ui.sh`. It can discover a compatible engine environment because source work may use different virtual environments.

Deployment uses one built runtime. From `delivery-api`:

```bash
scripts/build-dist.sh
./dist/requi-re/run-require.sh
```

`build-dist.sh` treats `delivery-api` as the build project. It expects the five Python package repositories and `vmodel-spec-app` beside it under the same `require/` folder, creates `dist/requi-re/runtime`, installs every executable package into that single environment, copies the shell support scripts, and writes `MANIFEST.txt` with every repository SHA, dirty-state marker, installed package version, build Python, and platform. Override the checkout root with `REQUI_RE_REPOS_ROOT`, the output with `REQUI_RE_DIST_DIR`, or build Python with `REQUI_RE_BUILD_PYTHON`.

The built launcher sets distribution mode and always starts Requi.re and its interview subprocess with `runtime/bin/python`. It does no interpreter discovery. Copy the complete `dist/requi-re` folder to a machine with the same operating system and architecture, then run `run-require.sh`. The launcher refuses an incomplete or incompatible copy with Error / Likely cause / Next guidance.


### Packaged real-model smoke

From `delivery-api`, build the real distribution, launch it through `run-require.sh`, drive a complete typed HTTP interview against local Ollama, and rerun the no-subprocess/stdin KPI:

```bash
scripts/smoke-packaged-real-model.sh --model-route=ollama
```

Use `--model-route=deepseek` when `DEEPSEEK_API_KEY` is available. CI or environments without a model can run `--launcher-only` to prove the built launcher, authenticated engine boundary, and static transport KPI without claiming a model result.

## Optional Turso embedded replica

Local SQLite remains the default. To keep local-speed reads and writes while syncing accounts and task state to Turso, create one database and token:

```bash
brew install tursodatabase/tap/turso
turso auth signup
turso db create requi-re
turso db show requi-re --url
turso db tokens create requi-re
```

Copy `.env.example` to `.env` in `delivery-api` (or beside `run-require.sh` in a built distribution), then set the values returned above:

```dotenv
TURSO_DATABASE_URL=libsql://requi-re-YOUR-ORG.turso.io
TURSO_AUTH_TOKEN=YOUR-GENERATED-TOKEN
```

Restart Requi.re. With both values present, the stable local database at `~/.local/share/requi-re/delivery.db` becomes an embedded replica: normal operations use the local file; libSQL syncs in the background every 60 seconds; account creation and session create/revoke also attempt an immediate sync. If the network or Turso is unavailable, local reads and writes continue and the deferred sync is recorded in `run.log` without logging the URL or token. Set `TURSO_SYNC_INTERVAL` to change the background interval. If either URL or token is missing, startup stops with a clear configuration error instead of silently running an unintended mode. Remove both variables to return to pure local SQLite.

The distribution build is incremental by default. It preserves the current runtime, hashes dependency inputs, and compares each repository SHA plus dirty diff hash with `.build-state`. Unchanged dependencies and packages print `boundary=... decision=skip reason=unchanged`; one changed repo rebuilds only that package. Use `scripts/build-dist.sh --clean` or `REQUI_RE_CLEAN=1` for a full rebuild. Every candidate build is validated in a staging folder and atomically promoted, so a missing repository, failed install, or failed import leaves the previous distribution runnable.

### Native build prerequisite for libSQL

Turso sync is optional and is not part of the local Requi.re runtime. To build a runtime that includes Turso support, opt in explicitly. The builder then asks pip for a binary libSQL wheel. If none exists for that Python/platform, the optional Turso build needs CMake plus Rust:

```bash
brew install cmake rust
```

The build checks this before dependency installation and stops with Error / Likely cause / Next instead of exposing Cargo/CMake output. Raw native-build output is saved to `dist/requi-re-build.log`. Because all work happens in staging, this failure leaves the last successful `dist/requi-re` launcher and runtime unchanged.

### Local CI test

Run `./scripts/test-local.sh` from any directory. It creates a reusable `.venv`, installs the development dependencies and runs the full local suite using Python 3.12. Set DELIVERY_TEST_SCOPE=pr for the bounded PR suite. Install Python 3.12 first, and make sure your Git authentication can read private `ShyAlon/vmodel-contracts` where it is a dependency.

For the local packaged app build, run `./scripts/build-dist.sh` from `delivery-api` with the sibling repositories checked out as described above. This builds the desktop distribution; it does not publish an image or deploy production. For a delivery-only container build, use the fenced Docker section above; no private git clone credential or engines are required.

For a standalone wheel build of this repository, run `./scripts/build-local.sh` (requires Python 3.12 and pip). This writes to `dist/` and does not publish or deploy anything.

### Bounded pull-request checks

CI runs every delivery-api core test plus a small browser set covering the
workspace, task states, account/gate navigation, live progress, cost and review
feed. It stops on the first browser failure instead of spending a timeout on
every dependent assertion. The job has a ten-minute outer limit. This is not
a full release check. Manual image-build/deploy runs use all browser tests
with a twenty-minute outer limit; PRs use only the browser smoke set.

Three tests marked `engine_integration` exercise the real interview-engine
boundary. They require interview-engine installed in the same environment;
the delivery-only PR check explicitly excludes them. The real package-export
test is marked `producer_integration` and requires a configured spec-generator
environment; it is also excluded explicitly, rather than silently skipped.
Before release, install the approved sibling engine and configure
SPEC_GENERATOR_DIR with its export environment and run the full local suite, including every
browser test, with `./scripts/test-local.sh`. No live model is required. The
four command-planning tests use a test-owned generator path and fake runners,
not a sibling generator environment. Nothing is quarantined as an unexplained
CI-only layout difference.
