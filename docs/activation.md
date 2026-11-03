# Activation checklist

Owner only. Ordinary customers do none of this. Checked against the current repo on November 3, 2026.

Real payments stay OFF until your pre-activation checklist and cleanup pass are complete. Starting the preview, merging code, passing a check or running one paid test does not approve payment receiving, public cloud activation or unattended work.

1. **Finish the code gate.** Use current main with full CI green. Ollama independence is merged. The acceptance entry and Back office readiness are merged. The single-limit change must merge before using the revised commands below.

2. **Review and apply missing schema yourself.** Run `req migrate` for dry review. If the exact plan is right, use `req migrate --apply` and its typed confirmations. The dry review must also show actual runtime SET memberships; review/apply repairs missing memberships even with no pending SQL. Separately review `req backoffice-access --test-owner`, then `req backoffice-access --test-owner --apply` only if its exact owner-test enrollment is approved. The owner test-task migration is `20261102220000_owner_test_tasks.sql`; it needs its own exact-file/hash `REVIEW TEST PURGE` confirmation. Do not assume merging applies it. This installs the guarded function; it does not purge tasks.

3. **Complete your cleanup pass.** Inspect current jobs, unresolved provider/compute evidence and database locks in Back office/Debug. Stop work through normal confirmed controls and reconcile unknown outcomes; never replay a paid call or treat unknown cost as zero. Do not run a destructive reset just to make checks pass. If cleanup needs a reset, review its exact dry plan and retained/deleted money scope separately before approving it.

4. **Verify owner policy and identity.** Use the existing read-only `req preflight --email YOUR_SIGNED_UP_EMAIL`. Resolve its named failures without disabling caps. If the confirmed expense policy is absent, `req policy` previews it; `req policy --apply` uses separate explicit confirmations and does not activate execution. Preserve your approved own-test protection, exact credit/expense scales and three owner-wide concurrent calls. Do not grant or buy credits automatically to satisfy a check.

5. **Review the paid model and limits in Back office.** Approve the default DeepSeek pin and current source prices/context/output bounds. Verify provider key/balance, positive exact-scale prepaid credits, owner admission and the installed runtime. Set a positive operator task-generation ceiling. This is the single acceptance provider-spend limit. You set $4 in Back office; the script reads the current saved value rather than taking a separate per-run ceiling. Change that limit in Back office if needed.

6. **Start the attended local preview.** Use `req preview --apply`, review its target and type its confirmation. Local Ollama needs no DeepSeek setup; paid DeepSeek still needs its own prerequisites. Verify the preview and private Back office are healthy. Verify alert delivery, Stop/reap and recovery evidence before broader activation; an outbox record is not proof that an alert arrived.

7. **Run read-only acceptance readiness.** After the acceptance-entry PR merges, run `scripts/ops/acceptance.sh --dry-run` from the repo. `req acceptance --dry-run` is a thin convenience route. It identifies local preview versus cloud, checks the owner session, marker migration/permission, default pin and ceiling, and prints the actual pin's cost-planning scenario. It reuses private saved session evidence or opens a normal sign-in window when needed; no token extraction prompt. No config edits, task writes or provider calls. No separate --max-usd is used. The Back office checklist is the same read-only readiness evaluator, not a new activation switch.

8. **Run the one attended paid acceptance, using the saved Back office limit.** After the single-limit change merges: `scripts/ops/acceptance.sh --execute`. This starts once, answers one text question and waits for another. It is not a full-interview/final-package certification. Keep the recorded target, pin revision, elapsed time, actual provider USD, retail credits/USD, usage and screenshot. It purges successful settled test content only; money/audit survive. A failure keeps data and tries to collect a private Debug ZIP. Do not retry blindly.

9. **Review results before expanding scope.** Confirm settlement, retained audit, cleanup, alerts and the remaining pre-activation checklist. Broader all-phase/final-package acceptance, cloud deployment, unattended operation and real-payment receiving each remain separate owner decisions. Keep real payments OFF until the checklist and cleanup pass are done and you explicitly approve that activation.

## Important limits

- A green fixture test is not a real paid-provider run.
- The dry-run cost is a scenario, not an expected measured bill or a maximum. The approved server ceiling controls provider spend; retail credits also include active compute.
- The HTTP timeout fix sets HTTP phase limits, not a total wall-clock deadline.
- No live database write, paid call, deployment, reset, payment activation or credit grant was performed to prepare this document.


### Owner session for acceptance

Run `bash scripts/ops/acceptance.sh --dry-run`. The script checks protected settings and any configured private browser-state file first. If no valid session is available, it opens a dedicated browser window at the verified target. Sign in normally in that window. Do not copy an access token, cookie, password or connection string into the terminal. A sign-in may create a normal authentication session; the acceptance dry run still starts no task or paid call. Cookies transfer only in memory and the window closes after the handoff. Your ordinary browser profile is not read.

Acceptance execution prints its current stage and a heartbeat every10seconds. No keyboard action is needed after the plan. It submits the scripted tutor answer automatically and can wait up to15minutes for each of two text questions (plus bounded HTTP/browser work). The task ID/link appears after creation. Ctrl+C stops the client, not server work; do not start a duplicate. To stop paid work, review the named test in Back office Worker Jobs and confirm Stop. A structured decision is refused, never auto-approved.

The engine answer request currently includes next-turn advancement, so its response can take longer than the CLI90s transport timeout. A lost answer response is not a rejection: the CLI never resends it. It polls the same task with GET only, within the original900s answer/next-input window including submission time, and continues only when a fresh text question has a different input ID. Working state or the old question alone does not prove acceptance. If that evidence never arrives, data stays retained and the owner must review before any retry. This does not retrofit a script already running.

Answer submission has a300s transport timeout within the unchanged900s answer/next-input window. This covers multi-minute end-to-end work, not a claim that provider generation itself takes300s. After the next question, settlement is checked with GET only for up to180s, every5s after each check. Output and failure evidence name fixed accounting blockers. No automatic Stop is sent, incomplete evidence is never purged, and expiry is not permission to repeat paid work. UTC stage/request timestamps support matching the same task to server submit/drain timings.
