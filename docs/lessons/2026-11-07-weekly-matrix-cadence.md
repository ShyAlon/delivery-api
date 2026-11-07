# 2026-11-07 - Full matrix weekly, not nightly

The fast-gate change (#694) kept full-matrix depth with a nightly schedule.
Nightly still buys ~31 billed minutes every day for a suite whose fast gate
already catches ordinary regressions per PR. The owner chose a weekly full
run instead: Sunday 02:41 UTC, deterministic, off-peak. ready_for_review and
workflow_dispatch still run the matrix any time, so depth is one click away
between weekly runs. If the weekly run ever finds something, the gap between
merges tells the story; raise cadence only with that evidence in hand.
