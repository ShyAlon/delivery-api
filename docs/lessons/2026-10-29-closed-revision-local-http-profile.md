# Closed-revision local HTTP checks need the intended import profile

2026-10-29: local draft-only revision and private-route404 checks toggle local
mode after app import. HeldPG-first already installs tenant middleware, causing
401 before the intended route/draft/fence assertions. Fail-first2failed/28passed
2.06s. Never remove installed middleware to hide the mismatch.

Move these two HTTP cases only to existing poisoned restrictedPG local-compat
subprocess import profile. Preserve original draft-only/no-dispatch/no task or
event mutation/unknown revision409 checks and every private GET/POST404 plus
AST/route/asset fence check. Direct-store closed review/preflight tests unchanged.
All30cases retained. No assertion weakened or coverage loss, no production or
default-bootstrap change. These are local contracts, not claims that anonymous
cloud requests reveal private route existence; cloud owner fences remain tested.

Revision + route-fence + cloudAPI slice Python3.11.17:66passed current6.77s,
66passed heldPG-first6.94s,1platformdirs warning each. HostedCI/full suite unrun.
No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
