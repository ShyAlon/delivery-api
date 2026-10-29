# Local control-plane startup needs its intended profile

2026-10-29: startup/no-recovery test implicitly expected local auth endpoints
fenced503. PG-first switches to cloud import profile, where exactly four auth
POSTs are intentionally allowed through the control-plane fence and CSRF returns
403 for its origin-less signup request. Fail-first1failed/7passed0.54s. Neither
403 nor auth pass-through is a production regression in that profile.

Run only the local startup test in existing poisoned restrictedPG local-compat
import process, making its local assumption explicit before app import. Preserve
health/profile/no-execution, no recovery/reconcile/queue startup, public home,
and exact task/signup/webhook503 assertions. Cloud four-auth-post pass-through
and local auth fenced tests remain unchanged. All8cases retained, no assertion
or coverage loss, no production/default-bootstrap change.

Control-plane + runtime-bootstrap + cloudAPI Python3.11.17:47passed current5.17s,
47passed heldPG-first5.46s,one platformdirs warning each. HostedCI/full suite
unrun. No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
