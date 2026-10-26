# 2026-10-26 - a selectable route without its backend env is a dead route

The preview wrote DELIVERY_LOCAL_PREVIEW=1 in both provider modes, so the
Ollama route stayed selectable in a deepseek preview; OLLAMA_BASE_URL was
written only in ollama mode, so choosing that route failed the task start
with a container-localhost connection error. Prevention: a route's enabling
flag and its required environment are written in the same provider branch,
and the cheap default is the zero-cost local route - the paid route stays
behind an explicit PREVIEW_EXEC_PROVIDER=deepseek.
