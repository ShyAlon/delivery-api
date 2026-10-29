# Browser refresh helpers need their clock import

2026-10-29: the decision-screen session-cost helper used time.monotonic without
importing time. Four real-browser cost/last-known/recovery cases failed before
reaching their refresh assertions. Add the missing test-only import. Do not
remove the visibility trigger or relax the displayed-price assertions.

Fail-first on unchanged merged main df556f2: 4 failed, 33 deselected (11.86s),
all NameError at the helper's first clock read. After import: 4 passed, 33
deselected (12.30s) with real Chrome/restricted PG; held PG-first 4 passed (12.44s), 33 deselected, is recorded
in the PR. Desktop/mobile session-cost screenshots inspected. No tests,
assertions, coverage, production code or default bootstrap changed.
Hosted CI and full suite not run. No deploy, external DB, model calls or spend.
