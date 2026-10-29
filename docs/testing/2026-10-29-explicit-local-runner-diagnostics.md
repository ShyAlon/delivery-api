# Make eight local subprocess diagnostic receivers explicit

The held PG-first default exposes eight receivers that directly exercise local ProcessRunner parsing with harmless Python scripts. They previously borrowed global CLOUD_TENANTS=False;under PG-first they instead enter cloud capability admission and fail before testing stdout diagnostics. These are not cloud worker/session tests.

Set local compatibility mode explicitly in each receiver,with its existing scopedPG storage. No HTTP middleware is removed or changed. Cloud worker capability,paid execution and tenant auth coverage remain separate and unchanged. No production change,assertion weakening or coverage deletion. No provider/model call is made.

Eight receivers:known-count stdout progress,stage-aware heartbeat,console prompt diagnostics,traceback/missing-module classification,every-question-line progress,fragmented module-name preservation,generator-stage attribution,and generator contract-error preservation. Every original exit-code/phase/gate/event/error/secret-hiding assertion remains unchanged.

Measured Python3.11.17:
- Future bootstrap before change8failed1.65s at missing cloud session capability.
- Today final8passed1.39s.
- Future final8passed1.08s;restored current conftest afterward.
- Expanded API/tasks/cloud/bootstrap157passed21.29s.
- Full suite/hostedCI unverified. Default flip remains held while other receivers migrate.

Lesson:local subprocess parsing tests need an explicit local execution profile even when storage isPG. Do not invent cloud session capabilities for tests whose subject is a legacy diagnostic parser,or let an import default choose their meaning.
