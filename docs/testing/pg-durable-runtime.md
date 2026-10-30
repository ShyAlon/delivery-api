# PG durable runtime checks

Use Python 3.11 or newer. Install delivery-api's dev dependencies and editable
checkouts of the matching interview-engine and spec-generator changes. Provider
models in these checks are deterministic fixtures, not live calls.

The ordinary test harness creates and migrates a throwaway PostgreSQL instance.
Never supply a live DSN. To run the focused storage and process boundaries:

```sh
python -m pytest -q tests/test_generator_pg_checkpoint.py \
  tests/test_generator_snapshot.py tests/test_generator_documents.py \
  tests/test_pg_generator_process.py tests/test_pg_revision_stage.py \
  tests/test_pg_execution_fence.py tests/test_postgres_metrics_runtime.py \
  tests/test_task_reset_script.py
```

The item6 connected graph has a separate isolation contract: the database name
must begin `requi_item6_` and use a `/tmp` socket. Its runner creates its own
server, rejects supplied test DSNs, applies the actual migrations and preserves
those guards:

```sh
python scripts/ci/test-item6-pg.py
```

It needs sibling source checkouts including their test fixtures, not just
installed wheels. The runner discovers editable package paths. This check covers
signup/auth, a complete graph, retained source review, private revision,
installation, four explicitly approved continuation phases, PG-only canonical
state, exact downloaded hashes, tampered export refusal and owner isolation.
It does not authorize or perform real purchases or production operations.

## Rollout order and remaining work

Merge the matching spec-generator runtime and interview-engine PG control PRs
before the delivery runtime PR, then build with all three updated revisions.
Apply the two new migrations and existing operator-managed role memberships as
an explicit later rollout. This change does not deploy or apply live SQL.

PG-backed tasks no longer depend on canonical artifact files or SQLite graph
checkpoints. Local PG-first bootstrap remains a separate held change. Resource
metrics remain explicitly selected with `DELIVERY_METRICS_BACKEND=postgres` and
a stable per-process `DELIVERY_METRICS_SOURCE`; missing/invalid PG configuration
must not silently fall back. Standalone legacy adapters and unselected metrics
still exist. This is not a claim that all file/SQLite code has been retired.
Auth, OAuth, browser sessions and email-availability code are not retired here.

The owner-only reset script is dry by default. It requires explicit owner and
live auth-session identity, refuses unresolved jobs/accounting and retains the
store's Stop/tombstone rules. The owner runs it after reviewing the dry plan.
Do not include its invocation in deployment or automatic startup.

Hosted CI and a production rollout remain unverified. The local acceptance
checks use disposable databases and fixture providers. Existing gated/skipped
integration tests remain separate coverage until explicitly enabled; a default
suite's skip count is never a green claim for those tests.
