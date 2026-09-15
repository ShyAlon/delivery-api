# delivery-api / V-model console

The thin local job-control server for the V-model CLIs, plus its reactive web UI. The server owns process lifetime, durable task/phase/event state, SSE progress, artifacts and rerun generations. It does not reimplement interview or specification logic.

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

Ollama is never in the image. Local deployments route to host Ollama; cloud deployments use API providers through the engines' shared model-routing environment. Engine CLI wiring is the next slice: `ProcessRunner` already holds the process, line stream and heartbeat contract.

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
