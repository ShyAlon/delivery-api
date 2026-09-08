"""The hard stop. The $5 question stays honest only if a runaway run dies
instead of running up the card. Per-stage budgets ride on the per-run cap."""
from __future__ import annotations


class BudgetExceeded(RuntimeError):
    def __init__(self, run_id: str, spent: float, cap: float):
        super().__init__(
            f"run {run_id} stopped by the budget guard: ${spent:.2f} spent of the ${cap:.2f} cap. "
            "No further LLM calls will be made for this run. "
            "To continue: raise the cap (DELIVERY_PER_RUN_BUDGET_USD) and retry, or mint a new link."
        )
        self.run_id, self.spent, self.cap = run_id, spent, cap


def check_run_budget(spent_usd: float, budget_usd: float, run_id: str) -> None:
    if spent_usd >= budget_usd:
        raise BudgetExceeded(run_id, spent_usd, budget_usd)


def check_global_budget(total_usd: float, cap_usd: float) -> bool:
    """False = refuse new runs. Existing runs still get their own hard stop."""
    return total_usd < cap_usd
