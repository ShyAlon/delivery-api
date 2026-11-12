# A live generator owns its task control state

The task read always passed `work=None` to the control projection. During
Stakeholder needs it instead queried the previous interview session, which was
COMPLETED. The UI therefore showed "Task needs attention" and "No live work"
while the actual generator was registered and producing output. This was an
ownership selection bug, not evidence of a dead child.

Task GET now queries the interview snapshot only during intent/CONOPS. For
later phases it projects a matching current ProcessRunner registration, including
decisions and resource waits. The registration is re-read even when the durable
task snapshot is cached. A running database row alone never supplies liveness.
Mismatched-phase and terminal-task registrations are discarded. Stop wins.
The existing owner-scoped task lookup runs before access to the runner registry;
no new database reads or writes are added. Internal process references are not
returned in active_work.

## Local proof

- Failing signed GET regression against disposable Postgres before the fix.
- Running Needs after completed interview, stale phase, orphan, decision,
  requirement, Stop, failed task, cross-owner isolation, warm cache cleanup.
- Real ProcessRunner registration and cleanup using a real local child stream.
  This diagnostic child does not call a provider or operate a user's process.
- Real authenticated task UI at 1475x780 and 390x844. No false warning for the
  registered owner; removing the owner and reloading restores the real warning.
- Existing HTTP, query budget, phase start, task and process-control tests.

No owner process was stopped, restarted or queried. No spend, deployment or live
database changes. This fixes control-state selection, not page layout: on phone
sizes the primary working detail is still below the long phase list. Visible
pending-decision actions and toast placement belong to the shared-layout work.

Gate results: 210 focused tests passed, including both browser viewports; fast
classifier split into two halves: 1010 passed + 1449 passed (2459 total), one skip and five
excluded integration/cosmetic tests. Hosted CI is not verified (budget exhausted).
