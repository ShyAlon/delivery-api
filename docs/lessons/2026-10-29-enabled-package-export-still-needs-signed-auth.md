# Local package semantics do not remove installed tenant middleware

Enabling the real-producer package tests under held PG-first bootstrap exposes
unsigned HTTP requests: both cases return401 before the expected409/200 branch.
The test deliberately sets CLOUD_TENANTS=False to exercise local-only export,
but middleware was installed at app creation. Keep that middleware.

Only test change: TestClient defaults now include the existing workspace's real
ES256 signed Authorization header. Every status, ZIP, immutable repeat and
historical download assertion stays unchanged. CLOUD_TENANTS=False stays.
No cloud export availability, auth, production behavior or coverage changed.

Before: held PG-first2fail.61s;current2pass3.21s. After:held2pass3.16s;
current2pass3.19s. Real generator export subprocess, no model calls.

Run tests in the process that owns pg_harness. A generic child-pytest runner
correctly fails workspace setup as external; that is an invalid environment
attempt, not a product red. It was replaced with the in-process owned harness
for both before/after current numbers. Hosted CI/full suite/deploy unverified.
No default flip, assertion removal, skip/timeout change or production edit.
