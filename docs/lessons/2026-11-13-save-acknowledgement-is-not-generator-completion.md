# Saving a gate and running the next phase are separate outcomes

The gate endpoint committed its reviewed decision and durable worker job, then
awaited dispatch_phase. That method runs the complete child phase lifecycle,
including open-gate continuations. The UI correctly waited for HTTP, but could
show "Saving" throughout generator work that had already begun.

Keep validation, gate, lineage and exact job enqueue atomic. Once committed,
use the existing owned background phase runner and acknowledge the decision.
The task progress page owns the long-running result, including later failures.
Do not add a client timeout/retry that can repeat a committed decision, skip
validation, or call a fresh provider to recover a slow HTTP response.

Tests must block the next phase, not only stub an instant dispatch. Check that
the HTTP response arrives while work is blocked, a fresh scoped PostgreSQL
reader sees the gate and exact queue job, no transaction handle crossed the
boundary, all three decision modes enqueue once, and a dispatch failure keeps
the gate decided while showing the next phase failure. Test owner scope too.
