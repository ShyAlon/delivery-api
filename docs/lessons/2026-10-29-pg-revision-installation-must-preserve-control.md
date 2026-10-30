# PG revision installation must preserve control and evidence

Replacing the graph saver is not a revision cutover. Review, private staging,
accepted installation, exact approval and every successor still depend on the
same checkpoint schedule and on byte-bound evidence. Moving only execution
leaves the normal full-feature flow on SQLite or silently drops safety checks.

Private staging is immutable evidence under its source session, not a prematurely
installed future engine session. Activation inserts the accepted destination
binding and copies the reviewed in-DB snapshot in the same transaction as the
parent generation CAS. A post-copy failure must roll back all destination rows.
Only that narrow accepted activation path can seed without a running worker.

Historical source verification needs an explicit read-only capability. It must
never become a worker capability, write saver or snapshot-capture grant after
the parent generation advances. Preserve the current owner/auth session check.

Stop recovery must check the same PG writer lease as execution. Keeping the old
file-lock check can falsely call a surviving accepted provider thread quiescent.
Retain the lease until the last accepted thread exits, even after timeout or
cancelled dispatch. Those threads still owe usage settlement.

Tests must remove all source/session files before review and staging, poison
SQLite, exercise real production graph nodes with deterministic fake model
responses, and reach each closed gate and terminal validation approval. Keep
fault coverage for revoked acceptance, expired auth, tampered source/stage/
anchor/prepared/result evidence, provider holds and transaction rollback.
