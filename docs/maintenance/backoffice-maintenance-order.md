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

## Worker Jobs follow-up

The table is now named Worker Jobs and has View plus binary-confirm Stop. Stop means durable Stop plus removal from the normal view; retained running/unknown money records are not reclassified. View reuses existing metadata/events/usage panels. Row rendering and request/error handling are shared with the existing tables. The new reusable confirm and busy/write helpers are infrastructure for further maintenance actions. Older sections still have separate paging/load implementations; consolidating all of them is a later explicit change, not an unannounced rewrite.

## Shared UI and local preview, 2026-10-31

Back office now shares the main app's fonts/icon/theme/chrome, table-row rendering, busy action lifecycle and bounded/redacted client evidence. Worker Jobs, Workspace, Accounts, Spend, Payments and Debug are hash-addressable keyboard tabs. Worker Jobs has one View/Stop/Delete menu for kebab, right-click and keyboard context-menu access. Stop keeps the task and saved work visible; Delete stops and hides it while retaining accounting and unverified outcomes. Suspend/Resume are not offered.

The private listener still has only its named read proxies and the existing confirmed worker-job Stop/Delete route. Browser evidence stays local to the tab; Debug is not a claim to include all server logs. No customer app boot, new data authority, schema change or guard removal is added. Existing per-table paging remains server-owned.

`preview-exec.sh` now launches its own Back office child after the preview checks pass, health-checks it, prints both URLs and stops it during cleanup. Default port8020; override `PREVIEW_BACKOFFICE_PORT` for a deliberate alternate. An already-running standalone Back office is not reused or killed. Same 127.0.0.1 host and signed-in browser are required. The child is stopped even when the preview container is retained after a failed smoke.

### Own-test transition evidence

The owner reported running `own-test-profile.sh` on main46df9ad at07:34 IST. The reported committed transition replaced the old2000000000000 reservation ceiling with NULL and selected the separate own-test $2 confirmed DeepSeek expense rolling24h profile, scale1000000000000 units/USD, `already_applied:false`. No execution started. This is owner-reported evidence, not an independently repeated live DB check or approval for another mutation. Paid children and the platform/session $3 guard remain parked; guard cleanup waits for the ten-guard decision sheet.

### Hermetic timing check

Five fresh Chromium contexts per version,1280x1000, localhost fake upstream, same current listener. Baseline inline shell at46df9ad: median Worker Jobs visible105.2ms (range95.9-140.0). Shared UI:174.4ms (168.3-225.0). This adds about69ms locally and loads shared fonts/modules rather than one inline document. It is not a live deployment performance measurement. No multi-second stall or telemetry gate was hidden or relaxed.
