# Local diagnostics needs its import profile

2026-10-29: two local diagnostic HTTP contracts set CLOUD_TENANTS=False after
app import. Held PG-first bootstrap already installed tenant middleware then,
so they receive401 instead of diagnostic data:2failed/10passed0.60s.

Keep the local model-call contract, including token counts/keep_alive, separate
from cloud's intentionally redacted projection. Move only these two cases to
the existing isolated PG-local import process, which selects local mode before
app import, poisons SQLite/Turso and keeps the real restricted PG database.
Unique task/session names prevent cross-process reuse of the fixed fixture
owner. Preserve all original assertions. No middleware removed or auth function
stubbed; cloud redaction and cloud owner-boundary cases remain exercised.
All12cases kept; no assertion/coverage loss, production or bootstrap change.

Diagnostics + cloudAPI + PG runtime bootstrap Python3.11.17:
51passed current6.24s,51passed heldPG-first6.42s,1platformdirs warning each.
HostedCI/full suite unrun; no deploy,liveDB,provider calls or spend.
