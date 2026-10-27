# 2026-10-27 - preflight: pg_catalog for existence, feature tables warn, named crash fallback

## What broke (second preflight regression in two days)

The #261 preflight refused a healthy production start twice in a row:

1. `[fail] required-tables` reported 11 tables "missing or not visible" on a
   database that had served the app for days. The check queried
   `information_schema.tables`, which **hides every table the querying login has
   no grant on**. Existence and visibility were conflated into one wrong answer.
2. `[warn] unknown` with `TypeError: str expected, not NoneType`. The
   provider-key check fed the engine's `_API_KEY_ENV` values into
   `os.environ.get`; `ollama` maps to `None`, and `os.environ.get(None)` raises.
   The crash fallback then labelled the check `unknown`, so the report named
   neither the check nor the cause.

## The test that would have caught it

Run the table check against a connection whose `information_schema` view is
privilege-filtered (the normal case for a restricted app login) while
`pg_catalog` shows the full schema - exactly what
`test_a_privilege_filtered_catalog_does_not_refuse_a_healthy_database` now does.
A unit test with an unfiltered fake made both worlds identical and hid the bug.

## The fixes

- Existence comes from `pg_class`/`pg_namespace` (never privilege-filtered);
  readability is a separate `has_table_privilege('requi_backend', ...)` probe.
- Tables are classified required-for-start (core: refuse when missing or
  unreadable) vs required-for-feature (FEATURE_TABLES: warn, naming the table,
  the feature, and the migration file that creates it). A missing optional
  table must never block the interview.
- `_check_provider_key` drops `None` values from `_API_KEY_ENV`.
- The crash fallback names the check that died instead of `unknown`.

## Rule

Existence questions go to `pg_catalog`; `information_schema` answers
"what can THIS role see", which is a different question. And a preflight check
must never crash anonymously.
