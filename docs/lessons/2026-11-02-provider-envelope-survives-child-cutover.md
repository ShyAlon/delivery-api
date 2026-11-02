# Exact bytes are not a complete HTTP request

The direct SDK transport forwarded body, headers and request extensions. The
child contract intentionally takes only provider/model/stage/exact bytes, leaving
endpoint and credentials with the parent. The parent reconstructed content=raw
without the SDK's JSON media type. A real DeepSeek request returned415, while
fake transports and actual-child fixture servers accepted JSON under any headers.

Keep one parent-owned request-envelope implementation for interview/generator,
local/paid and JSON/SSE. Preserve the admitted bytes without JSON reserialization.
Set required protocol headers in that implementation, never from child input.
Accept only the supported response media type; do not invent application/json
when it is absent. Non-success keeps status, HTTP reason and allowlisted vendor
code, not arbitrary body text that may contain secrets or project content.

Strict shared fixture providers reject the missing HTTP contract, including in
actual child Start/answer and generator JSON/SSE journeys. Existing accounting,
no-redispatch, exact replay, Stop and killed-child assertions remain. No live
provider or database was used. The real failed attempt remains unresolved; this
code change is not reconciliation or permission to replay it.

Separate finding: SDK timeout extensions do not cross the private body-only
boundary. Parent client/socket use3600s. Do not quietly forward child-selected
timeouts or claim that this envelope fix restores timeout parity. This requires
its own parent-owned timeout policy and tests.
