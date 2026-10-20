-- Read-only check after applying supabase/migrations/20261020080000_credit_ledger.sql in the Supabase SQL editor.
-- Expected on a fresh apply: table_exists true, write_trigger 1, append_only_triggers 2, rls_forced true, entries 0 (more entries once credits move).
SELECT
 to_regclass('public.credit_ledger') IS NOT NULL AS table_exists,
 (SELECT count(*) FROM pg_trigger WHERE tgrelid='public.credit_accounts'::regclass AND tgname='requi_credit_ledger_write' AND NOT tgisinternal) AS write_trigger,
 (SELECT count(*) FROM pg_trigger WHERE tgrelid='public.credit_ledger'::regclass AND tgname IN ('requi_credit_ledger_no_change','requi_credit_ledger_no_truncate') AND NOT tgisinternal) AS append_only_triggers,
 (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='public.credit_ledger'::regclass) AS rls_forced,
 (SELECT count(*) FROM public.credit_ledger) AS entries;
