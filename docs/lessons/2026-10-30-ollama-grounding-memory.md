# Grounding must release the resource it temporarily loads

The preview pin loaded phi4 with keep_alive=5m to inspect /api/ps, but never
unloaded it. A 16GB Mac kept roughly10GB resident after the check. The engine and
generator also default to30m idle retention for normal local calls, so preview
calls could keep that memory even longer.

Grounding now inspects first. If the model was already loaded, it reads the
context without altering its lifetime. Otherwise it loads for the check and
sends keep_alive=0 in finally after inspection, including failure. Failure to
request unload fails the pin with an actionable local command. The local preview
passes VMODEL_OLLAMA_KEEP_ALIVE=30s to both runtime processes and child calls.
This only changes idle retention, not active call budgets, routes or context.
A later local call may reload the model, trading startup latency for memory.

Fail-first tests verify temporary-model unload, failure cleanup, preservation of
already-loaded models and the preview's short retention. Actual Mac memory and
Ollama lifecycle behavior still need an owner-run check. Nothing was run on the
owner's machine. Ollama's API documents keep_alive=0 as immediate unload:
https://docs.ollama.com/api/generate
