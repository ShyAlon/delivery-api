# Requi.re

**From a real conversation to a spec you can prove.**

`delivery-api` is the web and job-control repository for **Requi.re**, plus its reactive UI. The sibling `interview-engine`, `spec-engine`, and contract repositories remain separately named implementation components of the Requi.re product. The server owns process lifetime, durable task/phase/event state, SSE progress, artifacts and rerun generations. It does not reimplement interview or specification logic.

## Run locally

```bash
cd ~/development/require/delivery-api
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python -m delivery_api
```

Open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/docs.

The UI has a V-shaped process map per task and distinct not-started, running and completed views. Live events use SSE, while SQLite keeps tasks, progress, audit history and artifacts through browser or server restarts.

## Local persistence and the cloud seam

SQLite is the default, not an API assumption:

```bash
export DELIVERY_DATABASE_URL="sqlite:////absolute/path/to/delivery.db"
```

All routes, background jobs and the subprocess runner depend on the `Storage` protocol. Only `SQLiteStore` contains SQL or a database connection, and `create_store(DATABASE_URL)` is the single backend selection point. A Postgres adapter can implement the same protocol and be selected there; the HTTP and CLI job layers do not change. Additive local schema migrations run when SQLite opens, preserving existing task/audit history. `DELIVERY_DB_PATH` remains accepted for older setups. Rerun policy is isolated in `Store.prepare_rerun`; archive is implemented as the reversible option, while wipe and overwrite remain blocked until the founder chooses.

## One image

```bash
docker build -t vmodel .
docker run --rm -p 8000:8000 -v "$PWD/.data:/data" \
  -e DELIVERY_DB_PATH=/data/delivery.db vmodel
```

Ollama is never in the image. Local deployments route to host Ollama; cloud deployments use API providers through the engines' shared model-routing environment. The task view starts the sibling `interview-engine` CLI, streams its live output and heartbeats, and sends browser answers back to the process over stdin. Set `INTERVIEW_ENGINE_DIR` when that repository is not a sibling of this one.

## Hetzner production deployment

Production is one multi-architecture Docker image on a small Ubuntu VM. SQLite is mounted from `/data`, outside the replaceable container. The container binds port 8000 to localhost only; put a TLS reverse proxy on ports 80/443 before public launch. Ollama is not deployed.

`CI and deploy` runs on every push to `main`: pytest plus Playwright, an amd64/arm64 image build, GHCR push, then optional SSH deployment. Deployment is off until the repository variable `DEPLOY_ENABLED=true` exists, so CI stays green before a server is provisioned. Images are immutable `sha-<commit>` tags. The remote deploy keeps the previous image name and prints an exact rollback command if `/health` fails.

### Provision once

1. Create an Ubuntu 24.04 Hetzner CX22 in an EU region. Add the dedicated deploy public key during creation. Do not paste private keys into the server or chat.
2. Copy and run the idempotent bootstrap as root:

   ```bash
   scp scripts/bootstrap-server.sh root@SERVER_IP:/tmp/
   ssh root@SERVER_IP 'bash /tmp/bootstrap-server.sh'
   ```

3. Verify the non-root deployment route:

   ```bash
   ssh -i ~/.ssh/vmodel-deploy deploy@SERVER_IP docker version
   ```

4. Set repository configuration from a logged-in `gh` CLI:

   ```bash
   cd ~/development/require/delivery-api
   printf '%s' 'SERVER_IP' | gh secret set HETZNER_HOST
   gh secret set HETZNER_SSH_KEY < ~/.ssh/vmodel-deploy
   gh variable set DEPLOY_ENABLED --body true
   ```

5. Trigger the first deployment with `gh workflow run 'CI and deploy'`, or push to `main`. Watch it with `gh run watch`.

The workflow uses `GITHUB_TOKEN` for GHCR. No registry password is stored. GitHub environments may optionally require manual production approval. Back up `/data/delivery.db` before database migrations; Hetzner VM backups are useful but do not replace an application-consistent SQLite copy.

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

## UI conventions

- Every view-level page header remains visible while its page scrolls. Use the shared `page-head` class, which sticks below the global navigation and preserves the current translucent surface treatment.
- Do not add a large promotional/marketing hero above a working collection. Put collection actions, filtering, and current source context in the sticky page header so the managed objects stay primary.

### Local accounts and workspace

`scripts/run-ui.sh` enables username/password accounts by default. Passwords require 12+ characters with uppercase, lowercase, number, and symbol, and are stored only as per-user salted scrypt hashes. Browser sessions use random opaque handles in HttpOnly, SameSite=Lax cookies; only their SHA-256 digests are stored. Set `DELIVERY_BASIC_AUTH_ENABLED=0` only when intentionally restoring the unauthenticated local development mode.

Accounts identify actions, while discovered artifact sessions remain one shared local workspace. This fits the single-machine founder-demo workflow and avoids duplicating or moving the source artifact tree per account.

Basic-account identifiers are email addresses. Older pre-email basic usernames remain in the local database for audit continuity, but must be recreated with an email address to use the current sign-in UI; OAuth accounts are unaffected.

## Development vs build/deploy

Development keeps the sibling repositories editable and uses `scripts/run-ui.sh`. It can discover a compatible engine environment because source work may use different virtual environments.

Deployment uses one built runtime. From `delivery-api`:

```bash
scripts/build-dist.sh
./dist/requi-re/run-require.sh
```

`build-dist.sh` treats `delivery-api` as the build project. It expects the five Python package repositories and `vmodel-spec-app` beside it under the same `require/` folder, creates `dist/requi-re/runtime`, installs every executable package into that single environment, copies the shell support scripts, and writes `MANIFEST.txt` with every repository SHA, dirty-state marker, installed package version, build Python, and platform. Override the checkout root with `REQUI_RE_REPOS_ROOT`, the output with `REQUI_RE_DIST_DIR`, or build Python with `REQUI_RE_BUILD_PYTHON`.

The built launcher sets distribution mode and always starts Requi.re and its interview subprocess with `runtime/bin/python`. It does no interpreter discovery. Copy the complete `dist/requi-re` folder to a machine with the same operating system and architecture, then run `run-require.sh`. The launcher refuses an incomplete or incompatible copy with Error / Likely cause / Next guidance.
