# Operator configuration check

Run from the delivery-api checkout, with its existing Python environment:

.venv/bin/python scripts/ops/operator-config-check.py .

The required positional argument is the repository whose settings to check. To
select one private settings file rather than the normal file-first sources:

.venv/bin/python scripts/ops/operator-config-check.py . --env-file /path/to/private.env

The normal loader reads ~/.config/requi/preview.env (or PREVIEW_SETTINGS_FILE),
then the supplied repository's .env, then shell fallback. Private files must have
mode 600. Files are parsed, never sourced or modified. No dependency installation
or interactive prompt is performed.

Output contains only fixed check names and true/false. No UUID, email, registry
version, capabilities value, database URI, password, file contents or exception
text is printed. Individual known Debug capabilities have presence booleans.
Exit 0 means the configuration checks passed; exit 1 means missing, invalid,
mismatched, or unverified evidence. Nothing is automatically repaired.

## Database inspection

An existing REQUI_ADMIN_DATABASE_URL is preferred for inspection; otherwise the
script tries DELIVERY_DATABASE_URL. Both come only from the settings loader.
No new access is requested or granted. The connection starts with
`default_transaction_read_only=on` and the transaction explicitly uses READ ONLY.
Queries have a five-second statement timeout; connection timeout is ten seconds.
The inspector neither sets a role nor calls business-effect database functions.
A separate read-only connection using DELIVERY_DATABASE_URL checks the actual
PostgreSQL current_user's SET membership in requi_operator_read, not the admin
login or the URI username. Supabase pooler usernames can have a project suffix
that is not a PostgreSQL role. app_role.inspected=false means this separate
connection/check failed; it does not prove membership is missing.

registry.table_present=false means the table was confirmed absent.
registry.inspected=true with registry.enabled_row_present=false means the full
registry was readable and no enabled row was present. registry.inspected=false
means the registry rows could not be verified, for example because the login is
restricted or RLS hides them. It does NOT mean the row is missing. Missing-row
booleans are omitted when inspection is impossible. database.connected=false
means no connection was established. check.completed=false indicates a loader,
connection, or query error, with private error details suppressed.

The registry is deliberately RLS protected; the app login alone normally cannot
inspect it. A preexisting authorized inspection connection may therefore be
needed to confirm presence. Never disable RLS or add privileges to make this
check green.

## Meaning and source of the three settings

REQUI_OPERATOR_SUBJECT is the canonical auth.users UUID matching the enabled
operator_principals.subject. REQUI_OPERATOR_REGISTRY_VERSION must match that
row's registry_version. REQUI_OPERATOR_CAPABILITIES is an explicit reviewed JSON
array of allowed names, all of which must already be allowed by the registry.
The check requires logs.read, debug.read, sessions.read and analytics.read for
the combined Debug capture. It does not print, generate, copy or save values.

The initial registry migration creates an empty table. The later
20261020130000_operator_stop_audit.sql conditionally registers the account
matching its literal email, using ops-v1 and four read permissions. It skips an
empty auth database and rejects a missing account in a nonempty database or a
different enabled operator. Its presence in the repository does not prove it
ran on this database, that the email belongs to the owner, or that settings were
saved. Do not rerun a mutation migration based solely on this check's output.

This is a configuration preflight, not proof that a signed-in browser session
is active or that every HTTP route works. Runtime reads still enforce the
signed-in subject, active session, capabilities and enabled registry version.
The command does not restart a server, interrupt a task, deploy, call a model,
allocate credits or write database/settings data.


## Fill missing settings from the existing registry

Owner-run, after reviewing the PR:

.venv/bin/python scripts/ops/operator-config-setup.py .

An optional --env-file /path/to/private.env selects both the input settings file
and the save target. Otherwise it saves to the standard private preview.env
selected by PREVIEW_SETTINGS_FILE or XDG_CONFIG_HOME. It requires both existing
DSNs and matching host/port/database. It never asks you to paste a DSN.

The admin inspection connection is READ ONLY. The script refuses an unreadable
registry, zero/multiple enabled rows, invalid identity/version/capabilities or
missing Debug reads. It copies the row's UUID/version and only the four Debug
read capabilities, never credit-allocation or other effect permissions. Before
saving it asks for yes in the terminal. No values are printed. A refusal makes
no file change. A successful save atomically replaces the mode-600 target,
preserves unrelated settings, rejects symlinks/nonprivate files and checks for
changes during confirmation. Only the three operator names are changed. No DB
writes, grants, restarts or automatic repairs occur.

Rerun the check afterward. This changes only local settings; an already-running
server will not use them until its next owner-managed restart. Do not interrupt
an active paid task to pick up settings.

## If operator-read role membership really is missing

The first version of this check used the URI username. With a pooler suffix,
that could falsely report app_role.can_set_operator_read=false. Run the corrected
check before changing privileges. It obtains the real identity through the app
connection and reports whether that identity can SET ROLE.

The existing 20261020130000_operator_stop_audit.sql already includes the narrow
grant for delivery_app, conditionally on that login existing. Do NOT replay that
whole migration to repair membership: it also registers an operator and changes
registry capabilities.

If the corrected check has app_role.inspected=true and
app_role.can_set_operator_read=false, first verify the actual app login is the
intended delivery_app. The existing owner identification command can establish
that without grants:

.venv/bin/python scripts/owner/apply-pg-cutover.py . --identify

Only after the owner reviews and approves that missing role membership, the
owner can apply this one narrow SQL statement through their admin SQL editor:

GRANT requi_operator_read TO delivery_app WITH INHERIT FALSE, SET TRUE;

This statement IS a DB authorization change, unlike the setup script. It is
not run by either operator-config command. If the actual app identity is not
delivery_app, stop and review it; do not substitute a pooler URI username or add
broad privileges. Rerun the corrected check after any owner-applied repair.
