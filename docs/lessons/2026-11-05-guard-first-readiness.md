# Guard-first readiness, November 5, 2026

Problem: owner preflight required uncertainty reconciliation globally and did
not read the current operator policy or actual confirmed-cost headroom. A green
policy-shape check therefore did not say whether Friday's next run could start.

Owner provenance: November 2, 08:28 WhatsApp asks to consolidate paths and make
the uncertain call a warning; 08:29 says "This is a reason for audit, not blocking
the entire server". Existing Nov2 SQL implements that behavior. November 5
08:14 asks to prepare full runs locally and on server. Keep the independent $2
own-test rolling, $3 session/platform and $4 operator exposure guard; no increase.

Fail-first: revised disposable-PG tests against unmodified main produced three
failures: provider uncertainty blocked, unresolved compute blocked, confirmed
headroom missing. Existing malformed settled-scale refusal still passes.

Fix: read-only repeatable-read snapshot, authoritative ledger sums (never lazy
projection refresh, which writes), exact policy/visibility refusal, active owner
work refusal, unknown outcome warnings, confirmed-cost headroom and first guard
for a NEW session. Existing session costs and uninspected existing-generation
exposure are explicit. Exhausted guard prints the report and exits nonzero.
No activation, money mutation, repair, provider call or reservation is created.

Do not rank exposure dollars as though they were confirmed charges. Do not call
a three-call diagnostic a full-run measurement. Do not derive current price or
run duration from a fast fictional-provider acceptance.
