# Showing a paid route is not approving its spend

A key alone does not make a route usable: installed gateway, reviewed model and engine route must match. req preview exposes both reviewed routes; preview-free remains local-only. Repo .env wins for provider key, with protected preview settings then shell fallback; missing values use hidden terminal input. Identity conflict handling remains unchanged.

Attended paid preview requires an immutable owner/Auth-session/task-generation/model-bound USD provider-expense approval. No default amount. The UI marks confirmation required; server refuses start without approval and checks every paid dispatch under existing global/owner locks. Settled costs plus all reserved/dispatched/uncertain worst-case exposure must fit. Since the paid request input bound is only an estimate, holds use full reviewed context ceiling plus output, at worst reviewed rates. Unknown usage never frees a hold. No automatic increase, model substitution, or retry.

This is provider expense, not a maximum customer credit bill: tokens plus compute may debit credits separately. UI says so. Local zero-priced calls do not need approval or credits. Existing positive-prepaid, vendor snapshot/balance, confirmed-policy and own-test guards remain in force. Approval persists across child processes; new generation/session/model needs new approval.

SQL justification: shared durable approval cannot safely live in process memory or be inferred from a launcher flag. One additive migration in this reviewed release batch; no live apply. Migration policy is docs/policies/migrations.md. Forward rollback: disable paid preview; do not delete evidence or drop the approval table while any relevant work is outstanding.
