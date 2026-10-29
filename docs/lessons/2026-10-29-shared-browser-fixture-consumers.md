# Shared browser fixture consumers must migrate together

2026-10-29: #426 removed the obsolete `ui` and `page` fixtures from
`test_ui_playwright`, but two importing browser modules were missed. On unchanged
main bbdb3bec both failed collection, so their five assertions never ran.

Port both modules to `pg_ui`, `pg_page`, and unique `pg_ids`. Debug coverage uses
an explicitly signed operator test identity with the seeded real auth session.
The durable evidence and retry UI responses remain intentional UI mocks.
No assertion, test, production code, or security middleware is removed.

Fail-first: collection of both modules produced two ImportErrors in 0.21s.
After: both modules plus PG browser fixture ownership guards: 10 passed in
14.27s on Python 3.11.17, Chrome, and disposable restricted PostgreSQL.
Inspected screenshots of completed ZIP collection, durable evidence, and rerun
refusal. Hosted CI and full suite were not run.

When deleting a shared fixture, search every importer and collect the full suite,
not only the edited module. SQLite-first collection can hide neither this import
failure nor the missing consumer migration.
