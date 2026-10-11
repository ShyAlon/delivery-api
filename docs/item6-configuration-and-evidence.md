# Item6 configuration, scaffolding and sandbox evidence

Status as of 2026-10-11. This note records the implementation and the validation
reached so far. It does not authorize a run, payment, production deployment or
cleanup. JEV-D13 is the existing accounting/configuration decision; this is an
operational record of that decision and the acceptance setup, not a new JEV ruling.

## What is proved, and what is not

Implemented and merged:

- JEV-D13 exact retail accounting and generated vendor evidence: delivery-api #109.
- Field/category diagnostics for current configuration review: #110.
- Passive private sandbox receiver, raw evidence adapter and script-driven IP TLS: #111.
- Explicit public ACME directory traversal and safe HTTP diagnostics: #112.

Observed on the owner's machines:

- The earlier D13 Mac image built. That is build evidence, not runtime evidence.
- The legacy configuration was migrated to a candidate and applied locally, with
  the original preserved as `.env.pre-d13`.
- A generated vendor snapshot was fetched successfully.
- Review first stopped on an invalid sandbox webhook-ID format. A read-only check
  named that field without printing its value; the merchant-ID format passed.
- The VPS passive receiver installed. Public IP TLS issuance, renewal dry-run,
  timer and trusted HTTPS checks passed after the ACME traversal repair below.
- The owner created the sandbox webhook subscription and updated the local source.
- A simulator notification reached the private spool. It remains **unverified**
  and proves delivery only, not a real capture or once-only credit posting.

The updated merged Mac image rebuilt, source diagnosis/review/confirmation/compile
passed, and dry preflight exited 0 on the intended image. It reported no network
start or database changes, with launch authorization false. The dry container was
preserved. This is preflight evidence only. A real sandbox capture, complete
attended chain, Gate/Edit, final artifacts and recovery are not yet certified.
Production payments and public launch remain separately gated.

## One human-edited source

The run's `private/.env` is a JSON-scalar file, not shell environment syntax. Never
`source` it. Values are parsed without interpolation or execution. Unknown or
repeated fields, malformed scalars, insecure permissions and symlinks are rejected.

Required fields:

- `MODEL`: the attended fixture/default model. Product model selection is per phase.
- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `DEEPSEEK_API_KEY`: local credentials.
- `SANDBOX_MERCHANT_ID`, `SANDBOX_WEBHOOK_ID`: reviewed sandbox bindings.

Optional overrides: `PRICE_PER_TOKEN`, `PRICE_PER_10MS`, `USD_PER_CREDIT`. Omission
uses the existing code defaults. A supplied invalid value is not omission. Keep
fractional overrides as JSON strings. The approved retail formula is:

```
tokens * PRICE_PER_TOKEN + deterministic_ms / 10 * PRICE_PER_10MS
```

Exact accounting uses 1,000,000,000 units per credit, with fractional-unit remainder
carried rather than lost. Provider expense is separate and does not set retail.
Provider/founder waits are excluded from compute metering.

JEV-D13 retires copied manual vendor pricing/band/context and legacy retail-unit
fields. `migrate-d13` writes a new candidate, preserves the filled original and
starts nothing. The owner inspects and applies it locally; migration is not a
reason to reimport, create another run or delete evidence.

VPS connection metadata belongs in a **separate** private local file, not this
exact-schema `.env`. The receiver helper uses
`~/.config/requi-receiver/connection.json`. The SSH private key stays on the Mac.
No keys, passwords, signed webhook headers or raw configuration belong in chat,
source control, ordinary logs or shared links.

## Generated vendor evidence

`fetch-vendor-snapshot` reads the fixed vendor pricing/API sources and creates a
hash-named private snapshot. It records source hashes, fetch time and parsed model
facts. Review requires one active snapshot matching the selected fixture model,
its filename/hash and the 24-hour freshness window.

The 128,000-token protective ceiling is an owner-approved run policy, not a claim
about the vendor's model maximum. Admission uses conservative request estimates
plus the hard output allowance. Unknown pricing boundaries, stale evidence or
changed sources hold new work. A changed vendor source is not silently accepted.

Preserve an existing successful snapshot while it is valid. If it becomes stale,
fetch and review current evidence through a separate staged step; do not overwrite
or delete old source evidence merely to clear a check.

## Scaffold, review, confirm, compile

The run root, private files and evidence are owner-only: directories 700, files
600. Each run has an isolated identity/database name. Before launch its identity
records `run_started=false` and `launch_authorized=false`, and evidence is empty.

The staging sequence is:

1. Build the exact pinned package/image; record source/wheel/image identities.
2. Preserve the existing source/run. Migrate only when its schema requires it.
3. Diagnose the current local source and snapshot with `diagnose-review`.
4. `review-config` validates it and returns a hash for the owner to inspect locally.
5. Confirm that exact current hash. This creates a private review receipt, not go.
6. `compile-config` creates a new private runtime directory and launcher/verifier.
7. Inspect image/container state and perform dry preflight with the intended image.
8. Start the connected attended run only on the owner's separate explicit go.

Review/compile do not contact PayPal or check wallet balances. PayPal resource-ID
format requires a JSON string of 1-127 ASCII letters/digits/underscore/hyphen;
format success does not prove the ID belongs to the sandbox app.

Diagnostics name `checked`, `expected`, `found`, and `where`. Values remain private;
fixed failure categories and field names do not disappear behind generic suppression.
`diagnose-import` is for the one-time legacy import path, not a cure for current
D13 review failures.

Compilation produces compatibility JSON and loopback PostgreSQL service settings
from the single source. Frozen runtime files are not edited by hand. The verifier
checks unchanged identity/source/runtime hashes and empty evidence before launch.
A changed source requires a fresh review and compile. Existing files are retained,
not overwritten to hide an earlier attempt.

Dry preflight starts no provider inference, sandbox capture or database changes.
The generated launcher leaves a named container behind, including after a dry
exit. Inspect and reconcile that container before another launcher invocation;
never blindly delete it, reuse a conflicting name or mistake a stale image tag for
the reviewed image. The raw-evidence adapter is copied as an item6 script into the
image, so its change requires an image rebuild even if the delivery wheel hash is
unchanged.

## Passive sandbox receiver

The Mac harness creates no public listener. The separate VPS receiver supplies the
missing notification evidence path without enabling the production payment API.

Data path:

```
PayPal sandbox -> public trusted HTTPS -> loopback receiver -> private spool
private SSH export -> new local evidence file -> attended sandbox verification
```

The receiver has no purchase/credit/engine effects, public export endpoint or public
receipt listing. Its ingress accepts only bounded `PAYMENT.CAPTURE.COMPLETED`-shaped
JSON with the five original signature headers:

- `paypal-auth-algo`
- `paypal-cert-url`
- `paypal-transmission-id`
- `paypal-transmission-sig`
- `paypal-transmission-time`

It rejects duplicate/invalid headers and duplicate JSON fields. It preserves exact
body bytes in the `item6-paypal-raw-v1` envelope using base64 plus a SHA256 hash.
The file remains `verified=false`, `sandbox_only=true`. Shape-valid forged arrivals
can be stored; storage alone never authenticates them. The bounded private spool
stops accepting when capacity is reached rather than silently deleting evidence.
Durable file/directory writes precede HTTP 2xx. Payloads/signatures are not logged.

Export uses authenticated, known-host SSH into a **new** private local file. Never
print exported data in the terminal or paste it in chat. The updated attended
adapter decodes raw bytes without reserialization. The older `{event,headers}`
format remains compatible but does not preserve original raw body bytes.

## Fresh VPS and public-IP TLS

The owner provisions and operates the box. Setup scripts are dry by default and
use explicit IP/user inputs. They require trusted SSH host identity, working key
login, root/passwordless sudo, supported Ubuntu, locally assigned public IPv4,
fresh destinations and unused listener ports. They do not reset existing services
or auto-accept SSH host keys. Partial installation is a state to inspect, not a
reason to rerun install or delete directories.

Caddy serves ACME HTTP on 80 and a narrow sandbox HTTPS path on 443. The receiver
port is loopback-only. Provider firewall rules and OS firewall rules are separate
checks. No shared DNS, public request-bin or third-party tunnel receives payloads.

No purchased domain is required for this path. Certbot with IP/webroot support
requests a publicly trusted Let's Encrypt short-lived IP certificate. Caddy loads
it explicitly; its default self-signed IP HTTPS is not used as a fallback. The
certificate lifetime is 160 hours. Renewal checks run twice daily with a reload
hook; setup tests renewal and public trust/IP, and requires more than 48 hours
remaining. Run the check again before attended capture. No separate ongoing alert
service is installed by this setup.

### ACME traversal lesson

The first TLS attempt received HTTP 403 from Caddy before certificate issuance.
Outside and loopback probes agreed. The public `.well-known` ancestor was root-owned
700, while its surrounding directories and challenge file were otherwise readable.
Caddy could not traverse that ancestor. This was not an ingress firewall timeout.

The narrow repair set only the public ACME `.well-known` directory to
`caddy:caddy`, 0755. No recursive permission change touched the private spool or
TLS keys. The permanent installer fix explicitly sets each public challenge
ancestor, rejects symlinks, checks readability as Caddy and reports HTTP403/404,
network failure or mismatch without printing challenge bytes. A regression starts
with an existing 700 intermediate and verifies private evidence stays 700.

## Sandbox subscription and attended capture

The owner registers the verified HTTPS endpoint under the **same sandbox REST app**
whose client credentials are in the local run. Subscribe to
`PAYMENT.CAPTURE.COMPLETED`, not an alternative-payment or billing event. Copy the
subscription's Webhook ID into `SANDBOX_WEBHOOK_ID` locally. A `WH-...` notification
ID is an event ID, not that configured subscription binding.

After the source changes, review, confirm and compile again. A simulator event
can establish delivery; it cannot pass for a real captured payment's signature
verification and must not be used as item6 evidence.

Do not create a separate manual purchase to obtain a convenient notification.
After dry proof and explicit go, the harness creates the run's single sandbox
order. The owner approves its URL as the sandbox buyer, then the harness captures
that same order. Only then does it ask for authentic notification evidence.

At that prompt, use a second Mac terminal to identify/export the real receipt for
that capture into a **new** file under the already-mounted run evidence directory.
Supply its `/evidence/...` container-visible path. Do not prepopulate evidence
before dry/start: the verifier requires it empty. Do not put the simulator receipt
there or edit frozen runtime files to bypass the check.

The existing processor verifies the original signature headers/event against the
configured sandbox subscription via PayPal. Capture/order/merchant and payment
facts must match. The service replays that event twice and rechecks the same capture
so duplicate events cannot post duplicate credits. Unknown capture outcomes stop
new purchases for reconciliation; a second purchase is not a retry mechanism.

Production uses a separately reviewed live app, verification/ledger/ingestion path
and operational proof. This passive sandbox receiver is not made production-ready
by flipping a mode flag. Clean-run acceptance, production payments and cleanup
remain distinct decisions.

## Packaging credentials

The locally prepared pinned ZIP and Docker rebuild do not need another GitHub PAT.
`ITEM6_SOURCE_READ_TOKEN` is only for the optional manual GitHub Actions package
workflow across the four private repositories. If enabled separately, use a
fine-grained, expiring token with selected repository Contents read-only access,
stored directly as that repository Actions secret. Never embed it in the package,
image, run source or chat. Workflow defaults are not a substitute for the reviewed
current four source SHAs.

## Source map

- `scripts/item6/single_config.py`, `vendor_config.py`, `review_diagnostics.py`:
  local source, migration, review receipts, generated runtime and diagnostics.
- `scripts/item6/prepare-package.py`, `Dockerfile`, `mac_run.py`: pinned package,
  image and dry/connected boundary.
- `scripts/item6/receiver/`: passive receiver, host/Mac setup and operational steps.
- `scripts/item6/attended.py`, `private_adapters.py`: buyer approval, raw receipt
  input, signature verification and once-only capture replay.
- `src/delivery_api/retail_pricing.py`, `retail_ledger.py`, `provider_snapshot.py`:
  exact formula/ledger and frozen vendor evidence.

Merged PRs: https://github.com/ShyAlon/delivery-api/pull/109,
https://github.com/ShyAlon/delivery-api/pull/110,
https://github.com/ShyAlon/delivery-api/pull/111,
https://github.com/ShyAlon/delivery-api/pull/112.

Vendor references:
https://developer.paypal.com/api/rest/webhooks/rest
https://developer.paypal.com/api/rest/webhooks/event-names/
https://letsencrypt.org/2026/03/11/shorter-certs-certbot
https://caddyserver.com/docs/caddyfile/directives/tls

## Appendix: scripts used in this setup

This appendix preserves the scripts in source form, rather than only describing
the commands. No host IP, sandbox bindings, run-specific IDs, credentials or
private evidence are embedded. Replace explicit placeholders using the owner's
reviewed local inputs. These are not a substitute for reviewing current state.

### Committed receiver scripts

The following versions are from the merged source snapshot used for this arc.
SHA256 fingerprints refer to script bytes, not a certificate or execution receipt.
The .txt installer was a transport wrapper containing these files; the maintained
source of truth is the repository script, not a base64 wrapper copied into a note.

| Script | Purpose | Repository path | SHA256 |
| --- | --- | --- | --- |
| `setup.sh` | Fresh-host install, IP TLS, renewal/check and separate SSH hardening | `scripts/item6/receiver/setup.sh` | `0659bc32d89f783c48b13f3ba2d90ce7ff8bbb53e3c6513293a570286d088e88` |
| `mac-setup.sh` | Strict SSH upload/run, private connection metadata, status/export | `scripts/item6/receiver/mac-setup.sh` | `93022d675f38afd538dd9831b77c314c9574df52c4e3bde05d23878daad30d5b` |
| `receiver.py` | Passive bounded raw-body/signature-header spool and private export | `scripts/item6/receiver/receiver.py` | `c90957b652f7dd19071fcc19e88e2f889c31584ab3dba121da58846bec461126` |

The ACME ancestor repair is now in `setup.sh`. The original one-line repair only
made the public `.well-known` ancestor Caddy-readable. Do not apply it to private
evidence or use a recursive permission change.

### Staged Mac blocks and dry invocation

Two companion source files are proposed alongside this note:

- `docs/item6-scripts/mac-stages.sh`: the five staged blocks used for extraction,
  hashes, new image build, local diagnosis/review/confirm/compile and container
  listing. It is a template with a placeholder repo path/image tag, not an
  unattended all-in-one installer. Run one reviewed block at a time and stop on
  any failure. It provides no connected-run option.
- `docs/item6-scripts/dry-preflight.sh`: a reusable equivalent of the actual dry
  block. Takes the existing run root, immutable image ID, compile suffix and
  unused dry-only name as explicit arguments. It runs the frozen verifier,
  mounts only compiled runtime/evidence and preserves the stopped container.
  It has no connected-run option and cannot supply go.

These templates remove private operational identifiers from the actual commands.
They are archived operational aids, not proof that an arbitrary host/image is
validated. The receiver export commands remain documented in
`scripts/item6/receiver/README.md`; signed data must go directly to a private file,
never be inlined into this appendix.

| Companion | SHA256 of proposed appendix file |
| --- | --- |
| `mac-stages.sh` | `e4c6a99e5940a64cf6889fe7712bdd50260a66891236f03a873f2211b7087d99` |
| `dry-preflight.sh` | `1bdc40f0d7d052f8c695ae5dcf8d57137481f7e408d2e9044381cd22f9cccfe3` |

The run remains stopped pending separate owner approval for the connected scope.
