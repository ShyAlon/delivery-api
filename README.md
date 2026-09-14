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
