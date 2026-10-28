# Shared review helpers need explicit storage selection

A helper imported by unmigrated suites must not silently pick up another test's
PG scope. Pass the workspace explicitly for migrated consumers and retain the
legacy default for the others until they are ported. Bind the unique fixture
session ID into the evidence context and semantic review too,or the test would
exercise a deliberately invalid session/evidence pair instead of its real case.
