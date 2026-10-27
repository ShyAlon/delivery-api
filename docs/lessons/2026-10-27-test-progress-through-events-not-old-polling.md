# Keep progress contracts when changing presentation and refresh cadence

The full 138-case task UI browser file and 37-case decision browser suite were
audited in bounded batches. Six tests depended on superseded presentation or
polling assumptions; one also exposed lost useful busy context.

- Review apply: #285 removed 'Recording decisions ... without resubmitting'
  from the compact renderer. Restore that context as its current line. Keep
  the original busy spinner, disabled button, committed success and failure
  assertions intact.
- Gate transition and two phase-change-dialog tests: mock snapshots changed
  without state events, then expected the old short poll to notice. Emit the
  engine's normal state events. Keep transition, pause, close and delayed
  reconciliation assertions.
- Question progress: a late heartbeat must not replace 'Waiting for your
  answer'. Assert the authoritative current line, open the collapsed history,
  then retain the original heartbeat visibility assertion.
- Background refresh: capture and drive the actual 60-second safety callbacks
  twice instead of sleeping for two obsolete 5-second cycles. Keep quiet
  background reads, continued snapshot liveness and terminal-output stop.

These test fixtures now follow #284/#285 contracts. No production polling
interval, timeout, money boundary or decision submission behavior changes.
No tests removed or skipped. Original review-apply test failed first; 37
review tests pass after the production context fix. All 138 task UI cases
were exercised, with each corrected case rerun together (5 passed).
This is not a claim that every repo-wide or hosted CI job was run.
