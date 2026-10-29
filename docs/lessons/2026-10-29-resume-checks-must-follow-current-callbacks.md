# Resume checks should pin behavior without stale callback names

Approved update of two static tests after merged timer fix. Fail-first unchanged
selection: two failed / two passed. Changed assertions, explicitly:
- Replace count of two exact noteActivity();refresh() handlers with exactly two
  handlers and membership of each actual balance.refresh()/refreshAll() handler.
- Keep ordering assertion, comparing noteActivity index to refresh substring so
  both balance.refresh() and refreshAll() are checked.
- Change timer regex from 30000 to 60000ms. Preserve exact count two and the
  assertion that neither timer calls noteActivity.

No behavior intent removed: visible resume records activity before refresh,
background timers do not. Existing executable real-source regression also checks
no timer activity, continuing balance reads, exact cadence, visible resume and
hidden-page behavior. No production changes, skips, timeout changes or deployment.
Hosted CI/full suite unverified. Four tests pass both profiles (runtime in PR).
