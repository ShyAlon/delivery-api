# Direct file-backed provenance tests need an explicit policy mode

2026-10-29: held PG-first selects cloud policy, so direct helper tests stop
seeing local saved-intent/usage provenance and local execution-detail projection.
Fail-first5failed/11passed1.19s in decision-provenance/model-routes modules.

These are direct helper contracts, not HTTP auth tests. Explicitly select local
policy in their real restrictedPG fixtures, leaving installed tenant middleware
untouched. Preserve every original founder source-turn/label, transcript model/
call/stage/fallback, debug-only execution details and pinned-route assertion.
All16cases kept, no assertion/coverage loss, no production/default-bootstrap
change. Cloud customer redaction and owner fences remain covered alongside them.
A local policy fixture is not used for HTTP: those need the separate import
profile because changing config cannot remove already-installed middleware.

Provenance + model routes + cloudAPI Python3.11.17:52passed current3.95s,
52passed heldPG-first4.10s,one platformdirs warning each. HostedCI/full suite
unrun. No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
