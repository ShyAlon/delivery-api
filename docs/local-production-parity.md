# Local manual testing and production differences

Verified against delivery c994085 and engine cb28814, 2026-10-29. This compares
checked-in launchers, not the deployed server or private settings on your Mac.
No claim of byte-identical private configuration is possible without inspecting
both effective configurations. This does not deploy or activate production.

Default entry: `bash scripts/local-test.sh /path/to/delivery-api`.
It runs the existing preview-rebuild/preview-exec path with both providers.
The downloaded copy accepts the same path and needs no mode/provider argument.
Optional explicit `inspect`, `execute deepseek`, or `execute ollama` remain.

## Removed differences

- Downloaded launcher defaulted to inspection: no key, execution closed, gateway
  absent. It now defaults to execution with both routes. Gateway installation
  remains automatic after the normal startup checks, not an auth bypass.
- Provider-pick prompt/default-narrowing: preview-exec defaults to both, while
  explicit PREVIEW_EXEC_PROVIDER can still narrow the routes. Missing either
  prerequisite stops clearly, with no automatic fallback or silent one-route boot.
- Local-preview flag suppressed phase-output reads and artifacts.zip even when
  files were available. Execution preview now uses the ordinary readers and
  ownership/path/integrity checks. Inspection-only restrictions remain explicit.
- Container memory/swap was 2GB, production go-live is 3GB: local now uses 3GB.
- Owner approved persistent local data: named volume requi-preview-data now holds /data, including metrics. Ctrl-C/rebuild never removes that volume. No existing data is migrated/deleted.

## Remaining differences and why

| Area | Local execution | Source-defined production | Status |
|---|---|---|---|
| Providers | Both DeepSeek and local phi4 Ollama by default | go-live requires DeepSeek; deployed managed routes omit Ollama without local flag | Requested local superset. Does not change production. |
| Model identity | phi4 default, override PREVIEW_EXEC_OLLAMA_MODEL | DeepSeek reviewed row; no local Ollama default | Requested local option; exact reviewed row must match engine. |
| Pins | DeepSeek public pages plus local server version/model/loaded context and both repo stage caps; composed without repricing | Same public DeepSeek pin, no local pin | Keep admission/context/price protections. Local grounding can load the model and use considerable RAM/time. |
| Execution | DELIVERY_CLOUD_EXECUTION=1, CONTROL_PLANE_ONLY=0 | go-live forces same; deploy-full alone intentionally OFF | Same admitted runtime as activated production, not a production activation. |
| Flags | DELIVERY_PREVIEW=1, LOCAL_PREVIEW=1 | Neither added by go-live | Needed for local origin support, Ollama/dual-provider gate and preview indicators. Artifact read restrictions no longer apply during execution. |
| Browser origin | HTTP loopback, port8001 default; localhost alternate allowed | Public HTTPS through Caddy, service loopback8000 | Keep. Local Secure cookie=0 vs normal secure=1; origins exact, no arbitrary-origin relaxation. |
| Networking | host.docker.internal maps to host gateway; local Ollama11434 | No local-model bridge in go-live | Necessary for Mac Ollama. No SSH in default path. |
| Image | Same Dockerfile.full, native Mac arch (usually arm64); main SHA tag, local build id | Server amd64 build, server build id | Keep native speed; PREVIEW_ARCH can explicitly select amd64. Not the same binary digest. |
| Source | Four clean main repos are fetched/fast-forwarded, candidate-tag smoke checked | Four main repos assembled for server image | Same source policy. No local-overlay support; PREVIEW_MOUNT_LOCAL has no handler. |
| DB/auth | Saved local preview.env, LIVE PostgreSQL/Supabase settings; real login/RLS | Private server settings merged from /etc/requi files | Same services only if settings identify same project. Values may drift. No server/private-file sync done. |
| Settings allowlist | Only DELIVERY_* saved settings, provider/API-key names excluded, forced local values win | Merges explicit server control-plane/token/model/key files | Secret-handling guard retained. Unknown custom/non-DELIVERY server values are NOT automatically copied. Needs explicit comparison/approval before widening. |
| Provider key | Existing exported key, else shared require/.env parsed as data; passed with docker -e | Server deepseek.env merged into full.env | Same key required, different protected storage. No shell eval, logging or key-file copying. Other provider keys stay excluded. |
| Engine token | Fresh per-preview random token | Persistent reviewed server token file | Necessary process credential, no reuse/disclosure of production token. |
| Artifacts/data | Persistent Docker volume requi-preview-data mounted at /data; historical server files not copied | Persistent /data/requi | Changed with explicit owner yes: container/temp-settings cleanup never removes data volume. Existing remote files need approved restore/sync, not blanket permission to copy. |
| Metrics | /data/requi-re/run_metrics.db on persistent volume | Same default data location | Changed from temporary metrics; no historical metrics copy. |
| Manifest flags | Saved DELIVERY_ARTIFACT_* values, no forced activation | Private production flags unknown | No activation/migration assumed. Existing files absent locally cannot be fabricated; DB-backed approved reads still apply. |
| Lifetime | Ctrl-C removes container/temp settings, retains named data volume, no restart policy; rebuild can replace preview and prune old local image tags | restart unless-stopped, previous container/env retained for rollback | Keep local lifecycle. Replace confirmation protects debug evidence/data; persistent-volume cleanup is manual, not automatic. |
| Resources/security | 3GB memory/swap, pids512, read-only root, cap-drop ALL, no-new-privileges, tmpfs256MB | Same checked-in go-live limits | Matched. Docker Desktop VM/host resources remain different. |
| Preflight | Local source syntax, startup tests, model pins, image smoke, live DB select1, contract/health, anonymous browser smoke if installed | Build smoke, go-live startup/open checks, remote health/rollback | Additional local diagnostics do not restrict product features; browser may be explicitly unverified if dependencies absent. |
| Admission/credits | Same user admission row, prepaid balance, concurrency and per-call reservation/settlement | Same | Kept. Local Ollama row is $0 but still ledgered. DeepSeek calls spend money/credits only when started. |
| Confirmations | Launch typed yes; replacing a running preview typed yes; normal in-app destructive/run decisions | go-live activation confirmation; same in-app decisions | Kept. Replacement can discard debug evidence, activation can enable paid work. No automatic yes. |
| Payments | No PayPal credentials, payment service or checkout activation added | Payment composition is separate from go-live/this preview launcher | Real PayPal remains off. Sandbox-only rule retained. No sandbox credentials provisioned or sandbox payment promised here. Any wiring requires explicit review. |
| Inspect option | Explicit inspection stays OFF and key-free | deploy-full pre-activation is OFF | Optional safe mode, not the default manual execution test. |
| Dev launcher | run-ui.sh still SQLite/local separate-engine dev profile | Not production path | Not selected for this workflow. PG-first test bootstrap remains held; no SQLite deletion/flip. |

No payment, destructive-data or secret-handling guard was removed. Still needing
an explicit decision: approved historical artifact restore/copy if desired; any additional
private server settings or sandbox-payment credentials to carry locally.

The launchers do not verify that a real user has credits until the normal app
checks, and launching an execution-capable container is not approval to make a
paid model call. No provider call or DB mutation was made in this verification.

## PG durable runtime follow-up (2026-10-30)

The table above describes the merged launcher baseline, not a completed storage
retirement. The PG runtime follow-up makes PG documents/checkpoints and exact
published artifact bytes authoritative for PG tasks, including cross-host
handoff, downloads and private revisions. No historical file import is needed.
The owner has since said existing tasks and storage-linked old code may be
removed. The owner-only reset script is dry by default and is not run by the
launcher. Legacy adapters and default metrics still remain for later retirement;
metrics PG selection is explicit. Local PG-first bootstrap stays held pending
the separate composer reproduction. See [PG runtime checks](testing/pg-durable-runtime.md)
for verification, merge dependencies and remaining limits.
