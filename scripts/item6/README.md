# Item 6: isolated connected acceptance on the Mac

This is a test-only path. It does not mount payment or private Gate/Edit routes,
change the production image, lift the launch fence, or authorize real payments.
The production `cloud_execution_admitted()` fence stays false. Use Docker Desktop
on the Mac because the existing pinned SQLite revision readers require Linux
`/proc/self/fd`. Do not run this entry point with native macOS Python.

## What has been proved

Hermetic source tests connect a fresh empty isolated PostgreSQL database through
29 exact migrations, restricted application/payment roles, fresh signed local
identity and active session, explicit operator registry, sandbox processor fixture
purchase/capture/verified-webhook replays/once-only credit posting, actual interview
and generator graphs, same-owner durable jobs and prepaid admission/settlement,
closed SRS change proposal, fresh reviewed SRS installation, all downstream
regeneration and new approvals, private ZIP readback and owner-scoped recovery.
Fixture rates/calls are labeled hermetic and make no external provider calls.

**Not yet proved:** Docker image build or execution on arm64/amd64, real DeepSeek
calls, real PayPal sandbox OAuth/approval/capture/webhook, or Mac host/container
mount behavior. Docker is unavailable in the preparation environment. An image
build or dry preflight is preparation, not a full acceptance pass.

Signup here is explicitly a disposable **local test-auth signup** with an ephemeral
asymmetric key and real database session check. It is not a hosted Supabase signup,
email verification, or customer production onboarding. The strict Supabase verifier
is unchanged; the local test signer is only constructed in this private script.

## Exact run shape

1. Fresh local signed test identity, fresh active session.
2. Explicit private operator registry and zero-balance account.
3. One USD 5.00 PayPal **sandbox** purchase for 5 credits (5,000 units).
   Founder approves in a sandbox buyer account. Capture must be confirmed. Original
   webhook event and headers must pass PayPal's signature-verification endpoint.
   The same webhook is replayed and capture rechecked, never another purchase.
4. Actual interview with founder text/ConOps concept choice/intent approval.
5. Full Needs, SRS, architecture, items, tests, validation, review and assembly.
   SRS and architecture wait for digest-bound founder decisions.
6. Founder supplies an SRS change request and extra-cost acknowledgement. The
   proposal is regenerated into a new checkpoint. Stale approval is rejected.
   Founder reviews a new same-phase result before installation. Architecture,
   items, tests and validation then each regenerate and wait for fresh approval.
7. Export original complete canonical bundle plus revised outputs/installation
   evidence. ZIP bytes are read back and checked against source hashes.
8. Check all jobs once-claimed/done, all provider attempts settled, installation
   files/database verified, and unrelated-owner visibility denied.

Success writes `result.json` with `ACCEPTANCE_EVIDENCE_READY`, never launch authority.
`HERMETIC_WIRING_PASS` means fixture evidence only. Any ambiguity writes a bounded
`blocked.json`, preserves receipts and stops. No background resume or blind retry.

## Prepare without running

Commit the tested harness snapshot first. From delivery-api:

```sh
python3 scripts/item6/prepare-package.py \
  --delivery "$PWD" --engine ../interview-engine \
  --generator ../spec-generator --contracts ../vmodel-contracts \
  --output /tmp/requi-item6-context
```

This builds the four exact package wheels, source SHA manifest and wheel hashes.
This test path does not use `build-dist.sh`, the launcher UI or verification-loop.
It uses only the four packages recorded in the manifest.

On the Mac, inspect the manifest and build:

```sh
docker build -t requi-item6-test /tmp/requi-item6-context
```

Build needs package/image download access. Do not interpret a successful build as
runtime certification. No production database, accounts or artifact volume belongs
in this container. It exposes no host port. The PostgreSQL server is loopback-only
inside the container and initialized only after exact run go.

## Local configuration, no credentials in chat

Make a new host directory for each run, mode 700, and a new empty `evidence`
subdirectory. Put local configuration/credential files in its `private` directory,
mode 600. Do not paste secrets, signed webhook headers or tokens into chat/logs.
Mount only these two directories, not the project workspace or home directory.
The container runs as UID 1000; the evidence mount must permit that user to write.
Confirm Docker Desktop mount permissions with dry preflight before go.

`private/services.conf` (no password needed for the isolated loopback-only DB):

```ini
[item6-admin]
host=127.0.0.1
port=55439
dbname=requi_item6_20261010_REPLACE_WITH_LOWERCASE_FRESH_SUFFIX
user=requi

[item6-tenant]
host=127.0.0.1
port=55439
dbname=requi_item6_20261010_REPLACE_WITH_LOWERCASE_FRESH_SUFFIX
user=delivery_app

[item6-payment]
host=127.0.0.1
port=55439
dbname=requi_item6_20261010_REPLACE_WITH_LOWERCASE_FRESH_SUFFIX
user=delivery_payment_test
```

Replace uppercase placeholders with a lowercase fresh suffix before use. The
actual database name must match `requi_item6_[a-z0-9_]{8,60}`. Use the same name
in all service entries and `run.json`.

`private/credentials.json` has exactly `client_id`, `client_secret`,
`deepseek_api_key`. These are sandbox app credentials and the local DeepSeek key.
Do not substitute live PayPal credentials.

Before go, separately verify the PayPal sandbox app's merchant ID and webhook ID
in the developer dashboard. The config records this reviewed binding; OAuth alone
does not discover or prove the merchant. The order's payee/amount/currency and
verified capture must then match it. Do not use a webhook simulator as proof of a
real captured payment. Preserve the real event with its original transmission
headers as a private JSON file containing exactly `event` and `headers`. The run
will ask for its path and verifies it against PayPal, rather than trusting the file.
No public listener is created here. An existing sandbox webhook receiver or manual
retrieval of the authentic event/headers is a prerequisite for this step.

`private/catalog.json` is a nonempty list of exact reviewed `ReviewedModelPrice`
records: provider/model/version/source, integer expense input/output/cache-hit/
cache-miss rates, integer retail input/output rates, expense units per USD,
1,000 retail units per credit, and usage categories input/output/cache-hit/cache-miss.
No price default is supplied. Use current vendor-published prices and your reviewed
retail rates. The same model must be selected for every stage.

`private/context.json` is an exact `ReviewedContextCeiling` record:
`model`, `context_tokens`, `maximum_output_tokens`, `source`, `bound_version`.
Verify current vendor model context/output limits. This conservative ceiling covers
the complete serialized SDK request, not a guessed tokenizer count. A ceiling is
not a guarantee that a prompt fits; a provider rejection stops with held uncertainty.

`private/run.json` example shape (replace every placeholder locally):

```json
{
  "root": "/evidence/run",
  "database_name": "requi_item6_20261010_freshsuffix",
  "database_host": "127.0.0.1",
  "service_file": "/private/services.conf",
  "admin_service": "item6-admin",
  "tenant_service": "item6-tenant",
  "payment_service": "item6-payment",
  "model": "REVIEWED_CURRENT_DEEPSEEK_MODEL",
  "catalog_file": "/private/catalog.json",
  "context_file": "/private/context.json",
  "paypal_secret_file": "/private/credentials.json",
  "verified_sandbox_merchant": "REVIEWED_SANDBOX_MERCHANT_ID",
  "sandbox_webhook_id": "REVIEWED_SANDBOX_WEBHOOK_ID",
  "operator_registry_version": "item6-reviewed-local-v1"
}
```

Choose a fresh 32-character lowercase hex run ID. Dry command:

```sh
docker run --rm -it --read-only --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --mount type=bind,src=/ABSOLUTE/NEW-RUN/private,dst=/private,readonly \
  --mount type=bind,src=/ABSOLUTE/NEW-RUN/evidence,dst=/evidence \
  requi-item6-test --config /private/run.json --run-id FRESH_32_HEX_RUN_ID
```

It prints a dry preflight receipt. It starts no database or network request and
creates no run evidence. Review Docker/source manifest, destinations, sandbox
binding, price/context source and the expected cost/stop conditions with Shy.

**Only after his explicit go for that prepared run**, repeat the reviewed command
with `--go FRESH_32_HEX_RUN_ID`. No script grants that approval. The exact run ID
prevents accidentally firing a different prepared run.

Progress is line-based ISO local-time timestamps, stage.state and a run ID.
Founder waits have no timer and no auto-approval. Read each private review file,
paste its digest and choose an action. Secrets never belong in these answers.
Original engine diagnostics can remain local; the main chain receipts are fixed,
bounded safe facts. Diagnostic token-cost totals are conservative upper estimates;
the raw-usage ledger with cache-hit/miss categories is source of truth.

## Stop, failure and evidence

Ctrl-C or a failed input stops the foreground container; accepted provider calls
may have reached the provider. Never assume interruption means zero spend.
Keep the original checkout/run/evidence. Do not create another purchase or reuse
the run ID/root to hide unknown capture or usage. No automatic cleanup is offered.
The wrapper archives the disposable DB privately as `item6-database.dump` before
normal exit/failure. A forcibly killed container may not archive; retain container
state and treat database evidence as unverified. Do not use `--rm` if preserving
container state after a hard-kill is part of the selected failure plan.

The revised outputs are separately labeled in the ZIP; the original canonical
bundle remains historical, not relabeled as the revised bundle. The private
revision machinery does phase-level validation and review; it does not run a new
post-revision final Assembly bundle. That scope is visible in the export.

After the actual clean Mac run, inspect `result.json`, all seven receipt files,
the private ZIP and DB archive. Cleanup/consolidation follows only after Shy sees
that run. Launch/Gate-Edit fence lift and real PayPal use remain separate decisions
and checks. Never infer either from this test result.

## Repeatable package automation (manual only)

`item6-package.yml` is a separate manual-dispatch workflow, never a PR/push trigger.
Choose four full lowercase 40-character source SHAs. Engine/generator/contracts
have the previously pinned defaults; delivery must be chosen explicitly, and must
contain the package builder. Inputs describe selected sources, not user approval.
The job checks all four checkout HEADs, builds offline wheels using preinstalled
build tools, verifies wheel hashes and ZIP readback, and uploads one
`requi-item6-package.zip` artifact with seven-day retention. It does not build a
Docker image, deploy, start a database, request payment or call a provider. Dependency
and build-tool installation still downloads packages. This is an exact source
snapshot, not a claim that arbitrary selected SHAs passed acceptance tests.

Private sibling repos require `ITEM6_SOURCE_READ_TOKEN`, a fine-grained GitHub token
with read-only Contents access to the four project repos, saved directly in the
repository's Actions secrets. No personal token goes in chat or the artifact. The
workflow fails before checkout if that prerequisite is missing, and checkout does
not persist credentials. The artifact contains no run credentials/configuration.
Dispatch is still an operator action; this change does not run the workflow.

After extracting the ZIP, generate templates without installing any packages:

```sh
python3 item6/scaffold.py create /ABSOLUTE/NEW-RUN
```

It prints only the fresh run/database identifiers. A new directory is required;
existing destinations and symlink parents are rejected. `private/` and `evidence/`
are mode 700; files are mode 600. The shell launcher is mode 700. No database,
Docker or network request starts. The private `webhook.example.json` is explicitly
an example, not authentic evidence. Fill the other files locally using the rules
above. Dashboard bindings, current rate/context sources and authentic captured
webhook event/headers still need manual verification.

```sh
python3 item6/scaffold.py validate /ABSOLUTE/NEW-RUN
```

Validation needs the built delivery wheel and its dependencies installed (use the
container instead when avoiding a host install). With remaining placeholders it
prints only filenames, never values. Structural readiness is not live merchant,
webhook or price verification, spending permission or runtime certification. It
also rejects reused evidence, redirected paths/services, unexpected roles, changed
run identity and insecure permissions. The actual container performs its own dry
checks again.

The generated `run.sh` uses only the private/evidence mounts and the fixed
`requi-item6-test` image. Its default is dry, with no `--go`. It keeps a named
container rather than auto-removing it. Inspect the dry result and mount access;
Docker build and Mac mount behavior remain unproved by this automation. Before
starting the separately approved real run, preserve/review the dry container and
remove that exited dry container manually to free the name. Never remove a partial
paid-run container just to retry. Only Shy's explicit go for the prepared run ID
allows adding `--go` and that exact ID to the launcher. The command-line argument
is an accident-prevention guard, not an approval source. Keys and go remain manual.
