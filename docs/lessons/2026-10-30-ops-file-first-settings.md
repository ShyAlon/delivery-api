# One protected settings boundary, not repeated credential prompts

The owner reported that copy-pasting keys is slow and error-prone. Local preview
read only preview.env while its provider key had to come from the shell. Owner
wrappers duplicated parsing and chose one file, provision always asked every
field, and go-live/grant-credits prompted separately.

Local commands now use one parser: explicit owner --env-file selects one source;
otherwise protected preview.env, then checkout .env, then missing shell values.
No dotenv shell execution or interpolation; no values on stdout or argv. Unknown
names such as PATH/BASH_ENV are not imported. Conflicting DB/API identity fields
across the two local files fail with names only rather than mix projects. File
permissions and syntax are checked before connection. Missing secrets use hidden
terminal input with no echoing fallback; persistence requires explicit yes and is
an atomic mode600 merge. Existing save fields are preserved.

The first8 tests failed before implementation. Extended coverage exercises child
exec transport, optional-save decline, literal dollar/shell syntax, file parsing,
identity conflict/fallback, container filtering, native resets and existing
preview/provision harnesses. The historical shell-only key tests were updated to
the owner's new file-first requirement while preserving no-secret-output and
no-start-on-failure assertions. No actual live operation or hosted CI is proved.

## Audited commands

| Command | Result |
| --- | --- |
| preview-exec | Shared file-first DB/API/key; missing-only hidden prompt; provider key passed separately, not Docker env file. |
| preview local / provision | Same loader; execution-OFF preview still strips model keys; provision reuses existing fields, saves after yes. |
| owner task-reset / stop-all | Same loader, saved RESET_OWNER_ID / RESET_AUTH_SESSION_ID; hidden missing IDs; pair persisted only after native active-session check. Typed dry review/Stop/Delete gates unchanged. |
| native reset / preflight / artifact backfill | Load settings before config; existing explicit owner/scope flags and dry/apply gates retained. |
| go-live | Local file key first, then existing server key; asks only if both missing. Server writes/restart still require existing separate yes gates. |
| grant-credits | Separate REQUI_ADMIN_DATABASE_URL setting, not silent app-login substitution. Credit plan and yes unchanged. |
| deploy-full / server preview | Existing /etc/requi server env files remain authoritative; no local file changes server target/account. No pasted-key prompt exists. |
| preview-rebuild / ops launcher | No credential input; dispatch into shared preview path. |
| pin / pin-local / combine-provider-pins | Public/local grounding, no secret input. Runtime config consumes environment selected by caller. |
| PG backup / restore drill | Explicit private PG service file required, no pasted credentials and no implicit target. Preserve isolation boundary. |
| smoke-packaged-real-model | Already reads parent-workspace .env; paid-model smoke not run or broadened here. |
| switch-model | Reference points to vmodel-spec-app/scripts/switch-model.sh, not one of the available checkouts. Not changed or claimed fixed. |

Owner IDs cannot be safely guessed from an admin allowlist or newest auth.sessions
row. No verified signed-in local session export was found. Saved explicit IDs are
reused and checked; an expired session still needs current-session input. No JWT,
password or access-token paste is requested.
