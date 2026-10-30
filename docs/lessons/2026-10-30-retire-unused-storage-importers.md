# No-migration policy means no dormant importer

The old historical-data planner and applicator only called each other. No
application, route, job, launcher or CLI entry point imported them. Their only
remaining callers were the planner tests and a tenant CI command; that command
also named an applicator test file already deleted in an earlier change.

The owner explicitly chose no history migration and approved removing old
storage code after its PG replacement. The merged PG task/session/artifact
paths are the active implementation. Retiring the unused import pair does not
run a migration, reset tasks or touch any database.

Deleted coverage is explicit: tests/test_legacy_ownership.py had seven collected
cases. They checked unmapped-row quarantine/read-only planning, explicit mapping
and digest, four conflicting/unknown/unbound/non-UUID mappings, and escaped-file
rejection. Those assertions test a removed import capability; they are not
claimed replaced by generic PG tests. The applicator test was already absent
before this PR. The CI command now points only to existing tests. New fail-first
checks prove the importer modules are absent and all referenced CI tests exist.

Live PG ownership/session/RLS tests remain unchanged. No auth/OAuth/session or
email-availability implementation is removed. Flake tracing is unchanged.
No live DB changes, data reset, deploy or spend. Hosted CI remains unverified.
