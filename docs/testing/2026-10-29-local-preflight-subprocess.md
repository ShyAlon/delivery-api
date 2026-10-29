# Isolate two local typed-engine preflight receivers

PG-first tenant middleware correctly gates start before local typed-engine checks. Two existing tests specifically prove built-runtime no-subprocess-fallback and human-readable local unreachable-engine failure. Keep their complete original HTTP/assertion bodies in named check helpers and invoke them through an explicit local-import subprocess profile.

The subprocess retains disposablePG DATABASE_URL,restricted app-role store,real scoped owner/session,and poisoned SQLite/Turso/sqlite3.open via pg_runtime_process. It sets local compatibility config before importing the app,not by removing middleware from the default app. Default signed tenant boundary and cloud refusal tests remain unchanged. No fake cloud installation/admission,production/default-bootstrap change,assertion weakening or coverage deletion. No engine/model/provider runs:preflight is unavailable and the runner fake must remain uncalled.

Measured Python3.11.17:
- Fail-first:built-runtime check failed in future tasks95passed5failed16.55s because typed engine was not reached;human-readable preflight failed in future API/decision10failed16passed2.64s because the detail was the cloud refusal.
- Today isolated receivers2passed2.58s.
- Future receivers+cloud API38passed5.32s;restored current conftest afterward.
- Expanded decision/API/tasks/cloud/bootstrap165passed24.18s.
- Full suite/hostedCI unverified. Legacy link and stale-answer receiver dispositions remain before the final flip.

Lesson:separate import profiles belong in separate processes. Keep local preflight semantics without stripping the real tenant middleware from a PG-first default app or pretending execution is admitted.
