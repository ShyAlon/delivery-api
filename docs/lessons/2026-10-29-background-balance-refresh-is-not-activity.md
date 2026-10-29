# Background balance refresh must not renew activity

The no-task header reused its visible-resume callback for the 60s timer. That
callback called noteActivity, making an idle open tab appear active. Use a
separate timer callback that refreshes the balance only while visible. Keep
60s cadence and visible-resume activity unchanged.

New executable JS regression on actual source fails before fix (1 failed in
0.16s) and passes after. It checks no timer activity, continuing balance reads,
exact 60s cadence, visible-resume activity, and no hidden-page activity.

Existing session_resume tests are unchanged as requested. Two static tests
remain red on both profiles: they expect old 30s timer syntax and two callbacks
named refresh(), not current 60s refreshAll/balance.refresh shape. Combined
result is 2 failed / 2 passed in 0.16s current; 0.47s held PG-first. No claim
of module green. No assertion removed/weakened; new behavioral coverage added.
No production deletion, deploy or bootstrap flip. Hosted CI/full suite unverified.
