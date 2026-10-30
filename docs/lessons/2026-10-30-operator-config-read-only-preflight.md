# Check operator configuration without inventing authority

A startup wiring fix cannot establish whether the owner's protected settings
match an enabled operator registry. Asking the owner for opaque names without a
safe check leaves them guessing, even when a prior migration may have created
the row.

Added a separate owner-run, boolean-only preflight. It reuses private file-first
loading, starts PostgreSQL read-only, and distinguishes an absent row from a
registry hidden by RLS. It checks the app login's role membership separately
from the inspection login. No identity inference, values printed, grants,
settings writes or registration are involved.

Fail-first: the new regression file failed collection because the promised
script did not exist. Tests then exercised the real disposable database:
read-only mode rejects INSERT, missing/matching/mismatched/reduced-capability
rows differ, and a restricted app login cannot turn RLS-hidden into missing.
Full-command coverage checks the loader, successful inspection, path argument,
private-file rejection, and suppression of connection/query exception details.

Do not equate migrations in source with live migration execution. Do not use a
failed preflight as approval to grant privileges or replay enrollment migrations.
