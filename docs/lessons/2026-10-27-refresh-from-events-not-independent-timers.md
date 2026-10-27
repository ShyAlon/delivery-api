# 2026-10-27: Refresh from events, retain a bounded safety read

A healthy stream did not stop the separate 30-second balance/cost timers. A
5-second output loop also fired immediately after page mount because its last
refresh timestamp began at zero. Measure requests after startup settles, not
by counting the first setup burst as idle polling.

Use one 60-second safety cadence for balance and cost/breakdown, one for the
task and its output, and debounced stream events for changes. Model-call end
refreshes cost/output without a task-state read. State changes still require a
fresh snapshot. Returning to the tab refreshes immediately; hidden tabs skip
safety reads. Event signals arriving during a cost read queue one follow-up.

An instrumented real browser with fake time measured the five read endpoints:
old main made one credit/cost/breakdown trio during the settled pre-safety idle
window; new code made zero. Crossing 60 seconds made one of each of the five
reads. The event regression then proved a cost refresh without a task poll.
This is a request count, not a claim about production milliseconds.

Do not explain the observed 26-56 second progress-retention lag as model time.
Separate fetch time, ingestion time, source age, engine Start return and final
progress drain in diagnostics before changing the engine's execution flow.
