# Maintenance through Back office

Reviewed direction, 2026-10-31: maintenance belongs in Back office, with diagnostics in Debug/operator pages. The first change is deliberately read-only. Existing scripts remain recovery tools until their UI replacements are reviewed; they are not started by this page.

## Existing coverage

| Operation | Back office/service today | Owner/script-only or remaining gap |
| --- | --- | --- |
| Find a specific task's jobs | Expand a session: metadata, job IDs, kind/state/generation (`debug.read`) | No title or owner-wide nonterminal-job table before this PR |
| Find owner-wide pending/running/paused jobs | This PR: My non-terminal worker jobs, titles, state, IDs, Stop evidence, paging | No liveness/stale inference or write action |
| Stop task / check Stop status | Existing task page and `/api/tasks/{id}/stop` / `/stop/reconcile` | Back office action not yet exposed. Stop All owner wrapper exists |
| Reconcile missing worker exit | Engine Stop reconciliation exists; generator-specific writer-lock recovery helper exists | No general reviewed Back office stale interview-worker repair. Do not mark a job complete from age/rebuild alone |
| Usage, credits, provider expense, holds | Existing account/session details, usage, expense totals, reservation release evidence | Grant UI lives on service operator pages; no automatic credit or settlement adjustment |
| Logs, diagnostics, evidence | Existing Debug export, task state events, failure diagnostics, operator pages | Back office does not yet gather one complete maintenance evidence panel |
| Own-test admission transition | Guarded `scripts/ops/own-test-profile.sh` | Readiness/blocking proof and reviewed confirmed transition need Back office treatment |
| Schema readiness / cutover | `scripts/ops/schema-readiness.py`, `scripts/owner/apply-pg-cutover.py`, Docker preflight | Add read-only readiness first. Migration remains explicit owner-run until a separate reviewed design |
| Task reset/delete batch | `scripts/owner/task-reset.py`; existing per-task Delete guard | No Back office destructive batch action in this PR |
| Preview setup/rebuild/deploy | Owner scripts | No service-side deployment or secret-management power added |

## Ordered next changes

1. This PR: owner-only nonterminal jobs with task titles and exact evidence. All reads use current account debug authorization, active Auth session and tenant RLS. Page every pending/running/paused job rather than silently limiting to the latest100 of all states.
2. Updated owner direction at07:06: binary-confirm Stop and Delete for the owner's own tasks. Persist Stop before hiding the task, preserve all provider/compute history and unresolved status, and do not label worker exit or settlement as verified. No running-work refusal merely to hide the task.
3. Offer the same confirm-and-stop-and-delete flow from own-test maintenance. Do not let the owner's retained running-job row alone block the transition after confirmed cleanup; preserve real accounting checks. Add readiness diagnostics.
4. Review individual maintenance mutations separately. Back office convenience must not bypass the existing accounting, Stop, role or schema fences.

A job stored as running is not proof of a live process. A Stop request is not proof of exit. A rebuilt preview is not settlement evidence.
