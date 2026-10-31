# Retire preparation expense shadows, keep the actual dispatch guard

C03: delete the unused expense_alarm_policy and operator_expense_snapshot preparations. No runtime called either module. The snapshot had held-cost and deleted-account assumptions that differed from the authoritative confirmed-expense PostgreSQL guard. Do not port their tests or install an alternative policy. Keep the existing transactional dispatch/settlement guard and its real-PG tests: confirmed session cost, platform rolling window, uncertain outcomes, scale, live owner, concurrent accepted completion and durable breach episodes.

Deleted tests cover only those retired preparation APIs, including their invented hold-count snapshot. Removed their CI file-list entries. No production admission, prepaid, own-test, tripwire, provider balance, model bound, concurrency, loop protection or accepted settlement behavior changed. No SQL migration or paid-child enablement.
