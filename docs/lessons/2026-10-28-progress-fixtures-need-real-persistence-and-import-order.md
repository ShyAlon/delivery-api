# Progress fixtures need real persistence and explicit import order

A cursor restart test must reopen storage independently, not reuse a fixture
object. The protocol regression now does so through a second restricted PG store
under the same tenant scope, preserving the exact deduplication assertions.

Poisoning SQLite before a late app import exposed the remaining global bootstrap
rather than a protocol defect. Import the app before the per-test poison, patch
module/app-state/orchestrator stores together, then keep the poison active across
the actual helper/runner tests. This is bounded fixture migration, not evidence
that global conftest has already become PG-only.
