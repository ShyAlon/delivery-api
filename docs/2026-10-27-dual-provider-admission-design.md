# Dual-provider preview admission 

Use separate single-provider factories. A dual factory accepts one reviewed row
per supported provider and a two-element provider-keyed context list. Legacy
single-row/context config remains unchanged. Only local execution preview can
admit Ollama. The local child receives no DeepSeek key, vendor hook, balance
reader or retail formula. No quote or price defaults.

Before worker invoke/compute metering, choose one child from the worker's durable
execution_options.model_route. Missing/unknown/ambiguous selections fail closed.
The child's reserve guard enforces the selection. The child's attempt read also
checks immutable quote provider/model before start, raw evidence or settlement.
A wrong quote is a reconciliation refusal, never reinterpretation with another
usage adapter. Compute rates come from the chosen child, never a factory-wide
paid rate applied to local work.

Interview run_step must pin a route in execution_options. Current program's
single effective model map owns subsequent steps. Construction uses the original
request if program does not yet exist. Route-switch candidate construction must
use the explicit target before installation, not the old program; a host-local
ContextVar around registry.switch_route supplies it and resets on failure.

Acceptance implemented and tested:
- Explicit preview `both` option composes independently pinned rows+contexts.
- Preserve DeepSeek-only no-Ollama-contact and local-only no-key behavior.
- Both-mode pin warns that local grounding loads the model once; no silent mode change.
- Hermetic fake-wire + disposable-Postgres alternating attempts prove local zero
  reservation/settlement and paid rates/vendor/balance. Verify compute segments
  select local-none vs paid-formula before invoke.
- Attempt replay/foreign quote and route switch tests; full affected suite.
- UI independent preflight, honest disabled reasons, no cosmetic enablement.

No real provider call, live DB access, deploy or restart during implementation.

Operational use after merge: pick option 3 at the preview provider prompt or set
`PREVIEW_EXEC_PROVIDER=both`. Keep the DeepSeek key in the existing protected
shell environment. Both-mode pinning loads the local model once; DeepSeek-only
option 2 remains available without that load. This change does not itself grant
approval for any real model call, live database change, restart or deployment.
