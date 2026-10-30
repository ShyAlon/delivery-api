# Pooler usernames are not PostgreSQL role identities

The first operator configuration check took the app login from its connection
URI. Supabase pooler usernames can include a project suffix that is not the
actual PostgreSQL role. This made missing membership a possible false negative,
so prescribing a grant from that result alone would have been wrong.

Fail-first regression used a pooler-style URI mapped to the real disposable app
connection. It reproduced the false result. The check now uses its own read-only
app connection and asks PostgreSQL about current_user's SET privilege. An app
connection failure is uninspectable, not missing membership.

An owner-run setup command reads the one existing enabled registry via an
already-configured admin DSN in READ ONLY. It validates the row, retains only
the four Debug reads, and asks for confirmation before saving three settings
atomically to the private target. It refuses endpoint mismatch, missing/hidden/
multiple/invalid rows, symlinks, nonprivate files and changes during review.
Unrelated file values remain intact. No DB mutations or grants are included.

The original read-only-check approval did not authorize running a file-writing
setup. This addition is delivered for owner review under standing PR authority;
the owner merges and runs it, and its separate prompt confirms the local save.
Do not replay the operator registration migration merely to repair membership.
Use corrected evidence first and review any narrow owner-applied grant separately.
