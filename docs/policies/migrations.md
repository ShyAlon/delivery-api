# Migration policy

Adopted November 1, 2026.

- Ship one reviewed schema batch per release, with one review/apply experience. Individual files can remain separate for atomicity and audit.
- Every schema change must state why SQL is needed, why code/config cannot do the job, its compatibility and rollback/forward-repair plan, and its validation.
- Keep the schema frozen between reviewed batches unless correctness, security or a measured need justifies an exception.
- Run fresh-database and upgrade acceptance before release. Typed confirmations name the exact files, hashes, destructive flags and target. No blanket destructive approval.
- Do not squash, reset or rebuild the live database. Owner test-data reset did not remove schema, auth, payments, policy or audit evidence.
- A fresh-install baseline may be prepared later, only after schema, RLS, grants, function and behavior equivalence tests. Preserve the upgrade path for existing databases.
