# Signed-out diagnostic transport must cross the tenant boundary

The sign-in shell sends browser errors to POST /api/client-log before a tenant
identity exists. The endpoint accepted these records, but TenantMiddleware
rejected them first with 401. A transport meant to diagnose sign-in failures
must be reachable while signed out.

Allow only the exact POST path in the existing public allowlist. Other methods,
child paths, and data endpoints stay private. The handler, validation and log
processing are unchanged. No database migration or live service change.

Regression: mount the real handler behind TenantMiddleware in cloud mode.
Before the fix its POST returns 401 instead of 204. After the fix it records
anonymous attribution, namespaces caller detail keys, rejects oversized
messages, and leaves other methods/paths protected.

Validation: 104 passed, 1 pre-existing failure across tenant HTTP, HTTP cutover,
auth/quota, log safety, debug-log copy and debug-path tests. The unrelated
model-route default assertion in test_debug_path fails identically when the
middleware is restored to the main-branch version. Its assertion is unchanged.
