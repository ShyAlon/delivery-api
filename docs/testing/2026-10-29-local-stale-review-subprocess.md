# Isolate the stale-review local answer contract

The held PG-first tenant boundary correctly gates answer execution. The existing stale-review test checks a different local contract:after review sequence changes,the HTTP answer is409 and no answer event is written. Preserve its original request/body/assertions in a named helper,executed using the existing PG-local import subprocess profile. Setup retains the same structured evidence snapshot and scopedPG store. The subprocess workspace now carries its live seeded scope as well as store;its import path includes the existing test helpers.

No default middleware removal,fake cloud admission,production/default-bootstrap change,coverage deletion or assertion weakening. Cloud refusal tests remain unchanged. No provider/model execution.

Measured Python3.11.17:
- Future baseline selected test1failed0.46s before review retrieval due unsigned identity.
- Today all8decision tests passed2.92s.
- Future decision+cloud API44passed6.14s;restored current conftest afterward.
- Expanded decision/API/tasks/cloud/bootstrap165passed25.10s.
- Full suite/hostedCI unverified. Four legacy-link receivers remain observed migration work;default flip is held.

Lesson:an explicit local stale-answer contract can be preserved in its own import profile without pretending cloud execution is open. A cloud503 and a local409guard are distinct boundaries,both worth retaining.
