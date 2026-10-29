# Blocked model progress must use the independent PG journal

The remaining HTTP receiver opened progress.sqlite while its real engine advance
was blocked. Move that receiver into the existing explicit-local-policy PG
subprocess with SQLite poisoned. Inject the same real process pool into
PgSessionStore with owner + live auth-session claims and select observational
mode. Never use engine-session id as auth-session claim.

Every original case-body assertion remains: task HTTP responsiveness .75s,
working/no-prompt control, responsive engine snapshot, heartbeat before release,
and completion. Add real production-migration FORCE RLS tests: another tenant
cannot read/append even when supplying owner A's identifiers; deleting the real
auth session immediately denies reads and appends on the same pool. Admin check
proves no forged/revoked rows landed. Disposable database only.

Requires companion interview-engine observational journal PR; this is not a
standalone change and must not merge first. Full HTTP cutover + cloud: 88 passed
26.64s current / 26.80s held PG-first. Authority selection: 6 passed 1.41s/.58s.
No original assertions changed/removed, no skips/deselections or SQLite exemption.
Dated engine lesson records fail-first new class tests and benchmark. Hosted CI
and full suite unverified. No production deletion, activation, migration/deploy.
