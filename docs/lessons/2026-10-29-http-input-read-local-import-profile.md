# Local HTTP input readers need a local application import

Changing CLOUD_TENANTS after app import cannot remove the tenant middleware
installed at import. Nine existing local HTTP input/error/rationale cases
failed with 401 before their assertions under held PG-first. Full diagnostic
before migration: 23 failed / 29 passed in 8.50s, including these nine.

Run these nine unchanged case bodies through the existing separate PG-local
import fixture. Real restricted PG remains; SQLite opening is poisoned. Local
profile is selected before app import, not by removing middleware. Existing
case assertions and test identities remain; no skips, assertion changes or
coverage loss. This is local HTTP compatibility coverage, not signed tenant
HTTP coverage. The cloud API module remains in the validation selection.

Current selected tests plus cloud API: 46 passed in 11.98s. Remaining start,
route-preview and async/local lifecycle receivers are still outstanding; this
PR does not claim the whole HTTP-cutover module is green. No production edits
or default-bootstrap flip. Hosted CI/full suite unverified.
Held PG-first selected tests plus cloud API: 46 passed in 12.06s.

Combined pending-receiver validation found a shared seeded-owner title collision
with the start case. Snapshot setup now includes its real unique session in the
title, with all behavior/assertions unchanged. After that fixture correction,
combined HTTP module excluding the separately blocked SQLite engine-progress
case passes: 51 tests. This combined check is not a full-suite result.
