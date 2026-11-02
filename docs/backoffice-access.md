# Reviewed Back office access

`req backoffice-access` is dry review. `req backoffice-access --apply` prints the
fixed capability/purpose review in the terminal and asks for
`ENROLL REVIEWED BACK OFFICE ACCESS`. It updates only the existing enabled sole
operator's registry capabilities and the matching protected operator settings.
It never enrolls/enables an identity, changes its version, grants database roles,
changes a price/limit, starts work, deletes evidence or settles a bill. Runtime
must be restarted by the owner to pick up protected settings. Use the normal
preview refresh only when no active paid work will be interrupted.

| Capability | Current workspace use |
| --- | --- |
| analytics.read | Account credit balances, provider settled/held spend, vendor status |
| sessions.read | Cross-owner session metadata |
| debug.read | Current-owner Worker Jobs, Stop/hide, server debug evidence |
| logs.read | Curated audit, release evidence; eligible owner-only resolution with debug.read |
| usage.read | Recorded tokens and customer credit usage in session detail |
| payments.read | Payment receipts, fees and net totals; read only |
| services.operate | Model price review, model defaults, operator expense ceiling edits |

Workspace/Orphans need the normal admin identity; Debug's local session report
needs no additional service capability. Cross-owner expanded detail needs
sessions.read, debug.read, logs.read and usage.read as appropriate. Owner Stop,
hide and audit resolution retain existing session/owner/proof checks; they do
not authorize arbitrary cross-user mutation. Pin/policy actions still have their
own diff/reason/confirmation. services.operate does not activate a paid run.

No credits.allocate, users.suspend, messages.prepare or sessions.operate is
added. No paid activation or write-off is granted. Those are separate decisions,
not a consequence of opening the private workspace. Existing reviewed local/DB
capabilities are preserved, including explicit permissions outside this bundle.
Future read setup preserves them only while identity/version and registry still
cover them; revocation refuses rather than silently resurrecting them.

The fixed review text names capabilities and purposes, never private values.
Result lines are fixed booleans. database.updated=true with settings.saved=false
means DB commit succeeded but the file save did not. A fresh dry review and rerun
can finish the local save; no rollback or atomic two-resource commit is claimed.
Concurrent registry/settings drift refuses. The app still checks its enabled
registry and current session at every request. Missing capabilities disable
private-page actions and show their names; this presentation is not authorization.
