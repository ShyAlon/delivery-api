# Item 6: isolated connected acceptance on the Mac

This is a test-only path. It does not mount payment or private Gate/Edit routes,
change the production image, lift the launch fence, or authorize real payments.
The production `cloud_execution_admitted()` fence stays false. Use Docker Desktop
on the Mac for the isolated PostgreSQL test harness and its fixed container contract.
The retired pinned file reader no longer requires `/proc/self/fd`.

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

## Fetch DeepSeek documentation as a proposal (no keys)

From the updated delivery-api checkout, choose an explicit model from the current
vendor pricing/API pages, then:

```sh
python3 scripts/item6/scaffold.py fetch-model-data runs/item6-run-01 --model MODEL_FROM_CURRENT_VENDOR_DOCS
```

This reads only two fixed public documentation URLs, no credential file or API key.
It writes mode-600 `private/deepseek-proposal.json`, with model/version, raw limit
labels, exact API output limit, both peak/off-peak USD rates, exact integer expense
conversions, source hashes and UTC fetch time. It prints those public fetched facts
and where they came from. It refuses to overwrite a proposal or reuse existing
run evidence. Redirects, changed table labels/spans/units, duplicate fields or
API/pricing disagreement stop without a partial proposal. The vendor publishes
HTML, not a stable pricing API; parsing is strict against the current documented
shape. If it stops, use the linked vendor pages manually and do not guess.

It does NOT overwrite `run.json`, `catalog.json` or `context.json`. The file contains
`run_model_proposal`, `catalog_proposals` and `context_proposal` ready for review and
copying, with `reviewed: false`. This keeps the original downloaded Docker image
safe too: a new metadata flag alone would not block its old entry point, so
executable templates remain unresolved. After reviewing, copy the chosen catalog
array, context object and model into their existing files; preserve the proposal
and mark its `reviewed` true to record your review. That flag is a record, not run
permission, and no script treats it as spending approval.

Human decisions remain explicit: numeric context limit (the table gives a shorthand
such as 1M, not an exact integer), current source/version confirmation, the applicable
pricing period, and your retail rates. Fill `price_version`/`bound_version` review
placeholders. Retail input/output are null and never fetched. No model is selected
automatically. Prices can change between fetch and the run; check before go.

A fixed catalog cannot faithfully price calls across peak/off-peak transitions.
The proposal preserves the full vendor rule, including holiday exceptions; it does
not choose today's band or silently apply peak prices as confirmed expense. Use
one reviewed applicable band only when the entire attended run stays in it; otherwise
stop and add per-call effective pricing before running. Network/model compatibility
and the actual run remain unproved by documentation fetching. Run the usual local
validation/dry preflight after completing the files. No paid provider request,
webhook request, database action or go happens here.

## One human-edited `.env` (preferred)

Keep `runs/` ignored in Git and check the ignore before entering credentials. All
run directories remain private, fresh and separate from source commits. The
legacy `create` command is kept for old workflows; new single-source runs use:

```sh
mkdir -p -m 700 runs
python3 scripts/item6/scaffold.py create-env runs/item6-run-02
```

It creates only a generated run identity plus `private/.env` and empty evidence.
The model is `deepseek-flash`, selected by Shy. A new directory is required. If you
already filled the old files, do NOT create another run or retype keys:

```sh
python3 scripts/item6/scaffold.py import-config runs/item6-run-01
```

This creates `.env` once, reading existing private configs locally. Old files are
preserved. Different DB names/models/repeated input prices, changed destinations,
wrong roles or insecure files fail without choosing a winner. Existing `.env` is
never overwritten. The previous schema did not record pricing bands, so the
import leaves that value unresolved rather than infer it from a number.

`.env` is the ONLY human-edit source. Every field appears once. The syntax is
`NAME="string"`, `NAME=123` or `NAME=null`; use JSON escaping for strings. Do not
source it into a shell. No interpolation, shell expressions, duplicate/unknown
keys, JSON arrays or objects are accepted. Secrets stay mode 600 and are never
printed. Model/DB repetition survives only in generated old-image compatibility
files, never as a second human-edit source. The database/run identity is generated,
not copied by hand. Price/bound version labels are derived hashes, not vendor API
parameters you must fill.

Fields with prices ending `USD_PER_MILLION` are quoted exact decimal USD strings,
not per-token integers. Input cache-hit and cache-miss prices are separate.
`CONTEXT_TOKENS`/`MAX_OUTPUT_TOKENS` are exact integer tokens. Retail fields ending
`UNITS_PER_TOKEN` are integers in the account's fixed 1,000-units-per-credit scale:
1 retail unit per token is 0.001 credits per token. These are your margin choices,
not vendor prices. No retail rate or unresolved context number is guessed.
`PRICE_SOURCE`/`CONTEXT_SOURCE` record the reviewed source URLs. `PRICING_BAND` is
`"peak"` or `"off_peak"`, reviewed against the full vendor timing/holiday rule.
A fixed catalog must not span both bands. This helper does not fix that run-time
accounting limit or grant permission to start a run.

The public-doc fetch creates the proposal as above. To generate a candidate `.env`
from a reviewed band and your explicitly checked numeric context ceiling:

```sh
python3 scripts/item6/scaffold.py apply-model-data runs/item6-run-01 \
  --band off_peak --context-tokens YOUR_REVIEWED_INTEGER
```

It writes a NEW `.env-proposal-*` file, preserves secrets/retail and keeps `.env`
unchanged. Existing differing values are conflicts, not silently replaced. Inspect
locally, then explicitly replace the source `.env` with that candidate if correct.
Nothing treats that replacement as review or spending approval. Fill the remaining
null values and review locally:

```sh
python3 scripts/item6/scaffold.py review-config runs/item6-run-01
python3 scripts/item6/scaffold.py compile-config runs/item6-run-01
```

Review shows only non-secret source values, source/unit/band conditions and a hash.
In a terminal, review-config asks you to type `yes` after showing the review. No
hash copying is needed. Enter, no, or EOF leaves it unreviewed. Noninteractive
review-config prints a read-only review and never confirms automatically. The
legacy exact `--confirm HASH` path remains supported for existing callers.
Confirmation records your exact unchanged local choice. Any source edit, including credential
changes, invalidates the compile receipt. It still does NOT grant run go, prove
sandbox bindings live or establish authentic webhook evidence. The compiler checks
all schema/scale/role/destination restrictions and generates a fresh mode-700
runtime snapshot containing the old image's exact JSON/services schema. Generated
files are mode 600. Existing files/snapshots/evidence are not overwritten or removed.

Use ONLY the returned `dry_launcher`, with no arguments for dry preflight. It
verifies the editable source and compiled file hashes before calling Docker, and
mounts only the generated runtime snapshot + evidence. `.env`/repo never enter the
container. Do not edit generated files; review/compile again instead. The original
image still works because the generated schema is unchanged. Each launcher keeps
the named container, so inspect/preserve its state; never blindly retry an unknown
paid run. The existing separate explicit go rule remains. No new Docker image,
provider request or payment is made by importing/reviewing/compiling config.

## Diagnose import without exposing values

```sh
python3 scripts/item6/scaffold.py diagnose-import runs/item6-run-01
```

Read-only, no dependency install or credentials output. It batches failures using
`checked`, `expected`, `found`, `where`: absent files, permissions, JSON syntax
line/column, required schema fields, existing `.env`, source-identity/model/role
conflicts and wrong rate/scale types. Arbitrary config values/unknown key names
are suppressed so accidentally misplaced keys never enter the report. Share only
this bounded diagnostic, never raw configs/.env or credential-bearing tracebacks.
Import runs the same preflight before writing. Other actions print bounded errors
instead of the old generic catch-all. Preserve existing source/evidence while
fixing the specific reported field locally.


## JEV-D13 accounting replacement (rebuilt image required)

The legacy fixed band and integer retail setup below is superseded for new runs.
Do not launch the pre-D13 image with a changed .env. Preserve the current run and
all private files. `scaffold.py migrate-d13 RUN` writes a separate candidate, not
an overwrite. It retains keys and sandbox bindings and removes copied provider
fields. MODEL is an attended fixture/default only; product selection is per phase.
PRICE_PER_TOKEN, PRICE_PER_10MS and USD_PER_CREDIT are optional overrides. Omitted
values use the approved existing defaults; an invalid explicit value fails.

`scaffold.py fetch-vendor-snapshot RUN --model deepseek-flash` fetches fixed public
sources without paid requests, generates source/hash/time evidence, and never
copies a rate or band into human .env. Review/compile bind the entire snapshot and
resolved retail formula. Stale or changed snapshot/source stops new admissions.

New accounting uses 1e9 units per credit, a once-only contribution ledger and a
retained fractional-unit remainder. Provider reservations are safe ceilings, not
confirmed charges. Uniform-band UTC intervals can settle using the frozen source;
unknown holiday/boundary facts hold settlement. Compute excludes provider and
founder waits. Failed measurement remains unresolved, never fabricated elapsed time.
Historic immutable payment intents and quotes retain their original scales. Active
account migration writes a separate receipt and refuses pending provider/payments.

The owner selected the simple estimate path. 128000 is enforced on conservative
input estimate (complete SDK wire bytes + 4096 framing headroom) + hard output cap.
This is NOT a verified input token bound or vendor maximum. The fixed provider
balance endpoint is read before dispatch; USD balance must cover the estimate,
CNY-only balances use provider is_available with no fabricated FX claim. Balance
reads are not atomic reservations across concurrent calls. Actual input overrun
settles once but records an overrun and stops new admissions/clean-run completion
for review. Explicit output-cap violations remain uncertain. No paid calls start
from config review or package build: the real run still requires separate go.
