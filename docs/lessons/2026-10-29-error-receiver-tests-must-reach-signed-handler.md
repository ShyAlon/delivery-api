# Error receiver tests must reach the signed handler

2026-10-29: held PG-first imports install tenant middleware. Two unexpected-error
tests previously sent anonymous requests and now stop at401, before their test
route raises. Fail-first2failed/1passed0.44s. Preserve the auth boundary rather
than deleting middleware or teaching the route to accept anonymous data access.

The temporary boom route now uses the existing realPG workspace + signed
pg_task_app middleware. Both requests carry that workspace's verified token.
Retain500/reference/type/redaction assertions and server-side RuntimeError
propagation. Temporary route cleanup is in finally. The independent public
static/health vs authenticated-data verification-count contract is untouched.
All3tests retained, no assertion weakened or coverage lost, no production edit.

Load-burst + cloudAPI slice Python3.11.17:39passed current3.50s,
39passed heldPG-first3.70s;1platformdirs warning each. HostedCI/full suite unrun.
No deploy,live DB,provider call or spend. Bootstrap restored and unpublished.
