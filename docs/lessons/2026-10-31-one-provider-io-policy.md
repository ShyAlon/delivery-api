# Sync and async use one provider I/O policy

C08: DeepSeek and Ollama factories copied client construction and used separate sync/async endpoint predicates. One validator now owns origin/method/path/credential/query/fragment checks before I/O. One factory builds both providers' admitted clients; redirects close and fail in both modes. No endpoint selection, paid-child wiring or billing policy changed.

A failing-first wire test covers both providers and both modes, rejecting credentials, foreign origins/paths and query overrides before send, plus redirects after send. Existing ledger/route tests remain unchanged. Filesystem containment remains a distinct later C08 slice; this is not a claim that all containment work is done.
