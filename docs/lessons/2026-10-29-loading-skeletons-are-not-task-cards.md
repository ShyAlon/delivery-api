# Loading placeholders need a separate identity from durable cards

The dashboard deliberately displays3skeletons before /api/tasks returns. They
carried class="task task-skeleton", so every real-card .task consumer (including
tests waiting for the first task) could accept placeholders and count3instead
of5. No tenant rows were missing. The full PG-first run exposed this timing.

Fail-first regression parks /api/tasks, asserts3skeletons but0real cards,
then releases the response and requires5cards/5V-maps. Before fails3!=0 in3.07s.
Fix removes only .task from skeleton markup. Shared CSS base changes .task{ to
.task,.task-skeleton{, preserving its layout/card appearance. Skeleton-specific
rules and real-card hover/child rules stay unchanged. No old assertions edited,
new sleeps, timeout/skip changes or coverage removed.

New regression+existing dashboard/filter+loading geometry tests4pass11.49s
current /11.42s heldPG-first. Final regression3.01s held. Loading and loaded
screenshots inspected:3styled placeholders then5real task cards, no clipping.
Hosted CI/full suite/deploy not run on this head. No default flip/live DB.
Composer progress click flake is separate and not fixed speculatively here.
