# Sign decision evidence read, draft and comment receivers

Five decision-page behavior tests and the PG fixture guard now supply existing signed pg_task_app and owner headers to setup. Preserve explicit local engine snapshot compatibility,structured evidence/digest revisions,retained old reviews,draft identity,comments not answers,bound purpose/session evidence,semantic findings,and same-sequence conflict409 with original retained. Every assertion remains unchanged.

The setup helper accepts a keyword-only signed_app;the sole remaining stale-answer test still uses its previous current-default path pending separate answer-policy migration. No new unsigned path is introduced. No middleware removal,production/default-bootstrap change,coverage deletion or assertion weakening.

Measured Python3.11.17:
- Future bootstrap excluding stale-answer baseline5failed2passed0.77s.
- Today all8decision tests passed2.14s.
- Future excluding stale-answer7passed2.07s;restored current conftest afterward.
- Expanded decision/API/tasks/cloud/bootstrap165passed23.13s.
- Full suite/hostedCI unverified. Stale-answer409needs separate handling because the tenant boundary correctly gates answer execution.

Lesson:decision evidence inspection and non-answer drafts/comments can use actual signed requests without pretending cloud answer execution is available. Keep that boundary visible instead of stubbing admission to make all decision tests look alike.
