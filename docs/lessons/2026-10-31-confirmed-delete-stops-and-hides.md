# Confirmed Delete stops and hides, never proves exit or settles money

C10: customer Delete and Back office maintenance now call the same task-based Stop+hide transaction. A job adapter supplies its own task identity. Every task keeps its rows, source artifacts, checkpoints, events and accounting, including empty tasks. Durable Stop fences new work before hiding; a hidden task does not imply worker exit or resolved charges. Accepted calls retain their normal settlement continuation.

HTTP requires an exact boolean confirmation. The existing customer dialog now explains retained evidence and possible late billing. Internal explicit delete calls remain confirmation by invocation; the shared command itself defaults to no confirmation. Replays retain the first Stop generation and cannot touch another owner's task.

Tests that asserted Delete refusal for provider history or Stop pending were superseded by retained-ledger and accepted-after-hide settlement assertions. A third assertion that repeat Delete raised was replaced by idempotent replay. Fixture teardown no longer tries to finish a hidden job as a fresh worker. No assertion about owner isolation or accounting correctness was removed.
