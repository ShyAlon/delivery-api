# Operator local HTTP checks need the intended import profile

2026-10-29: three local operator HTTP tests rely on local admin dependency
overrides or local /ops shell access. PG-first app-import middleware is already
installed before their later config toggle, so the requests stop at401.
Fail-first3failed/24passed1.15s.

Move these three cases only into existing poisoned restrictedPG local-compat
subprocess import profile. Preserve all original nonadmin refusal/admin health,
zip MIME/name/content/integrity, no-store/validation and static shell/script/body
assertions. Preserve the same test-only require_admin dependency override,
not a claim to test cloud admin authorization. Standalone signed-in middleware,
redirect/revoked-session and cloudAPI owner fences remain exercised unchanged.
All27cases retained; no assertion weakened or coverage loss. No production,
default-bootstrap or actual page design change.

Operator-pages + ops-health + cloudAPI Python3.11.17:63passed current6.60s,
63passed heldPG-first6.54s,1platformdirs warning each. HostedCI/full suite unrun.
No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
