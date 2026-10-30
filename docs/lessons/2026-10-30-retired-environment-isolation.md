# Retired settings must not break required PG startup

The preview pin step imported config after dotenv discovery and refused a
nonempty TURSO_DATABASE_URL. The preview's generated container env already
allowlisted DELIVERY_*; its host-side pin still inherited shell/checkout dotenv.
The reset wrapper hit the same late config import. No retired store existed,
but the obsolete negative guard kept making old local settings operational.

A shared PG-only runtime helper now neutralizes every TURSO_* variable after
dotenv loads, reports names only, and leaves explicit empty values so later
non-overriding dotenv loads cannot revive known names. Config imports use it,
covering ops, pin and installed container/server entry points. The owner-run
reset wrapper imports the same helper. Required PG URL and Supabase validation
are unchanged; no alternate store is enabled or contacted.

Fail-first subprocess tests reproduced shell and dotenv contamination. Tests
verify unknown retired names, idempotence, unchanged PG/auth settings and no
value disclosure. Focused gate23 passed; broader ops/preflight/full-entry/reset
and no-engine-smoke gate134 passed. Actual shared pin.py succeeded with fake
retired shell settings, fresh public documentation GETs and owned disposable
PG. No live DB, model call, Docker execution, preview launch or deployment.
Hosted CI and full suite were not run for this change.
