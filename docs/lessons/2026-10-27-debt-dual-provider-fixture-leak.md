# Debt: dual-provider fixture leaks retail contributions into later tests

Open, outside child phase 3. Reproduce with the ordinary disposable PG harness:

    .venv/bin/python -m pytest tests/test_dual_provider_ledger.py tests/test_retail_ledger.py -q

12 pass, 1 fail: test_failed_contribution_does_not_debit_or_drop_remainder
expects zero retail_contributions rows and sees two. Reproduced identically on
clean delivery main e2deeb561a56524c167ae3e3cca160f3380ec727. Retail suite alone
passes. The dual-provider test posts two token contributions but its reused
ledger fixture cleans attempts/limits, not those contribution rows. Parent
transaction/RLS behavior is not implicated by the evidence.

New phase-3 real-ledger broker fixtures clean their own contribution rows;
new broker+compute+retail batch passes together. Do not weaken the old zero-row
assertion or silently exclude its failure. Separate item: tighten the existing
dual-provider fixture teardown, retain all assertions, run both files together,
and prove disposable task/job evidence is removed without altering live data.
