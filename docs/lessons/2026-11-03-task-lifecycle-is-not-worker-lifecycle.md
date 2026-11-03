# Task lifecycle is not worker lifecycle

The owner's acceptance task was absent from Worker Jobs while its captured control state still said running. That historical bundle did not establish its current state or ownership. Reading the query showed a separate reproducible defect: only pending, running and paused worker rows were listed. An interview worker can finish between input turns without completing its task.

The owner-scoped list now also includes the latest worker by generation and creation order for running, waiting_gate and stop_requested tasks. Terminal workers on inactive tasks stay absent. Test-task markers are displayed data, never an exclusion. Existing nonterminal rows remain visible even across generations for diagnostics. Liveness, exit, accounting settlement and quiescence remain unverified.

A regression test fails against the original query after a marked owner's task keeps running and its worker is done. The fixed test covers input/gate/stop lifecycle visibility, foreign-owner isolation, and confirmed Stop using the retained row. The browser fixture presents the terminal worker as a still-active task and keeps View/Stop/Delete available through the existing confirmed flow.

No live task state, owner mismatch or exact timeout operation can be inferred from this code fix. No production writes or provider execution are part of this change.
