# Engine-client unit calls need their trusted-call context

2026-10-29: nine MockTransport serialization/error/timing unit tests assumed
local mode and lacked a tenant context when PG-first selected cloud mode.
Fail-first9failed/12passed0.76s. Do not turn off the cloud boundary to reach the
mock engine. Supply the authenticated-call context the client requires.

Add explicit engine_scope fixture only to the nine affected unit tests. It
provides synthetic owner/auth-session IDs without a persistence or identity
verification claim. All traffic is MockTransport. Existing no-scope refusal,
owner-header provenance and stop owner-scope tests intentionally remain outside
this fixture; no autouse fixture hides their negative cases. Preserve every
original typed response/error enum/token/preview/UUID path/diagnostic-header/
timing assertion. All21cases kept, no assertion/coverage loss, no production or
bootstrap change. Full real identity/owner boundary proof remains in cloudAPI.

Engine client + request-cost + cloudAPI Python3.11.17:57passed current3.59s,
57passed heldPG-first3.79s,one platformdirs warning each. HostedCI/full suite
unrun. No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
