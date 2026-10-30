# Operator Debug needs production wiring, not a fixture-only reader

Meditator7.97 capture at18:43:41 showed accepted202, running intent/session,
a settled provider attempt and a new dispatched attempt. It did start. File
logging was healthy, but every operator evidence route503-refused because
app.state.operator_read_store was never initialized in production. Only tests
and image fixture scripts installed it. Do not mistake missing evidence for no
provider execution, or retry/start another task as diagnosis.

Fail-first: real application lifespan with explicit operator settings still made
_operator_reader raise503 before this patch. After patch it installs the reader.
Missing/malformed/local config clears stale authority and stays disabled. Startup
prints presence/status only. Each read still checks exact principal, capability,
active session, and the live enabled database registry version. No grant is
inferred from admin email, no row is inserted and no permission expanded.

The settings boundary also dropped all three REQUI_OPERATOR_* names. Now exactly
SUBJECT, REGISTRY_VERSION and CAPABILITIES survive file-first loading and the
preview's two allowlists. No arbitrary REQUI_* setting or provider key enters the
container file. Missing values do not trigger guessed identity or permissions.

Required protected settings are the already-reviewed operator identity/version
and capability array matching the database registry:

- REQUI_OPERATOR_SUBJECT: canonical verified auth subject UUID
- REQUI_OPERATOR_REGISTRY_VERSION: exact existing reviewed registry version
- REQUI_OPERATOR_CAPABILITIES: JSON array of already-authorized capabilities

Do not invent these from the admin badge or copy a fixture UUID. Code does not
create/enable a database operator principal or grant roles. Unknown/missing row,
SET ROLE grant, capability, version or session still fails closed. Rebuild alone
cannot fix absent operator configuration. Never ask for passwords/keys in chat.

Once current work is explicitly quiesced and owner is ready to replace preview:

    cd ~/development/require/delivery-api && git pull --ff-only origin main && bash scripts/ops/preview-exec.sh

Existing preview name must first be stopped/removed through the approved owner
flow, not automatically while a provider attempt is live. This command builds
and starts execution-capable preview; it does not start an interview. Keep the
terminal open. Confirm startup 'Operator reads: configured', sign in as the
verified operator, open the affected task's Debug and Download everything ZIP.
If settings are absent, startup says disabled and Debug names the three settings;
if database registry denies, repair requires separate reviewed owner action.

164 focused tests passed, including actual lifespan, real disposable PG registry
revocation/wrong principal/inactive session, server file read, real bash preview
filter, and existing capacity/read/diagnostic suites. First expanded tests found
2 capacity fixtures injecting authority before lifespan; changed them to explicit
startup configuration, preserving original assertions. No skips or relaxed
permission checks. Hosted CI, owner Mac, Docker image and live registry/config
were not run. No live writes, restart, deploy, provider call or spending.
