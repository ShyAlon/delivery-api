# Unknown signal time is not epoch zero (2026-10-28)

Review of the A63 screenshot found 'last engine signal1793210671s'. The synthetic
progress event had neither ts nor payload.timestamp. stageProgressHtml fell
back to zero,then subtracted it from current epoch seconds. This is a real
renderer edge-case exposed by a fixture,not proof of an old engine signal.

Signal age now requires a finite positive source timestamp. Missing/zero/invalid
times omit that age;stage timing can still fall back to run start without claiming
an observed signal. Valid payload ISO time and event epoch seconds retain their
precedence/fallback. Existing progress text and elapsed counter remain visible.

Fail-first regression:4failed/3passed12.68s;after fix60passed0skips20.85s across
signal age,event progress,UI bundle,cloud API and bootstrap. Final seven browser
cases after screenshot addition7passed0skips12.96s. Inspected rendered progress:
100s elapsed and Drafting remain;no invented signal age. This small product fix
is separate from A63 fixture migration. Hosted CI/full suite/deployed topology
unverified;no deploy or live database touched.
