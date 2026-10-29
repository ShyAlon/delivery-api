# A live task callback must not inherit the next page's epoch

Browser trace: approval review mounted, then75-78ms later task.detail overwrote
it while its URL stayed on that review. Old task snapshotRefresh started at epoch2
while its captured task epoch was1. It called detail after navigating; detail
captured the new epoch, so its precommit guard did not help.

Fix retires the task's EventSource/timers onPageExit and checks captured epoch
before snapshot reads, after their resolution and before reporting an old error.
No assertions, skips, timeouts or old coverage changed. New actual browser test
starts a hermetic real interview, answers3turns, submits concept and asserts the
new approval form survives1s and task layout is absent. Before1fail9.76s;after
1pass10.00s current /10.32s PG-first. Adjacent SPA suites22pass74.37s current.

Five original completion/host-handoff acceptance tests enabled and passed both
profiles. Current timings:reference12.29s,handoff23.92s,pickup21.10s,full40.75s;
refusal passed in two-case66.24s run whose pickup attempt timed out GET30s and
shutdown10s. Pickup's isolated rerun passed; that intermittency is not erased.
PG-first five tests:3pass57.48s+2pass60.75s. Combined module tests hit the fixture's
daily-grant guard; run different modules separately, do not weaken that guard.

After screenshot inspected: approval content survives, task layout absent. Full
page sticky-header geometry is not certified by this lifecycle fix. Hosted CI,
full suite and deploy not run. No production deletion/default flip/live DB.
