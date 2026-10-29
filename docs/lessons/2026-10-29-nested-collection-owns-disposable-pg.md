# Nested pytest collection must own its disposable PG

Cosmetic-membership bookkeeping launches pytest collection in a subprocess.
It inherited DELIVERY_TEST_POSTGRES_DSN from the outer harness. The held
PG-first bootstrap correctly rejected that as external before collection,
so two membership checks failed instead of examining actual collected IDs.
Fail-first: 2 failed / 1 passed in 1.72s.

Remove only that inherited test DSN from the subprocess environment. Nested
pytest starts its own disposable migrated PG; no external database is used.
Do not weaken the bootstrap's external-DSN refusal. Keep exact cosmetic
membership, no-extra/no-missing and smoke-pattern separation assertions intact.
No production edits, skips, selection changes or coverage loss.

Current module: 3 passed in 4.41s. Held PG-first: 3 passed in 5.60s.
Hosted CI and full suite unverified. No browser is launched by this module.
