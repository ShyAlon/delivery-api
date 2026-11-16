# Gate work areas, evidence channels and superseded producer rows

A viewport-fixed action strip covered the app header and dialogs. Placement now
belongs to the shared Page work-area grid. The original buttons remain in their
original forms; only layout grouping changes. Actions are the first work-area
row, not persistent viewport chrome. Successful refresh clears only the refresh
error, never an action result, stale-revision warning or draft.

An empty technology-preference match did not mean the founder supplied no input.
Show exact preferences, hash-verified retained constraint snapshots and model
reasoning separately. A source quotation cannot approve its ADR. Match support
findings to the exact ADR/source digest. Missing published findings remain
unreviewed questions, not fabricated ledger records or disposition.

A synchronous phase retry POST can remain pending after its producer starts.
The dialog now hands off after a new phase command beyond a freshly read event
boundary. Keep the original request and duplicate guard alive through settlement;
report a late failure. Running task state alone proves no new launch.

Failing-first browser checks exposed two Enter defects: auth accepted duplicate
submit events while fetch was pending, and shared dialogs called requestSubmit
without respecting a disabled primary. Guard both shared paths. Tab/password/
Enter works in the tested Chromium environment; a specific Mac Chrome failure
was not independently reproduced. Preserve names-only diagnostics.

An old RUNNING job can outlive a newer successful job and falsely block a waiting
gate. Reconcile only the exact settled task/session/generation/phase with an
expired lease, a latest DONE successor, no newer activity or other current job,
and no unsettled old accounting. Under task/session/job locks, retire the stale
row as FAILED with a supersession audit. Do not pretend it completed, refund,
approve, dispatch, rewrite artifacts or touch accounting. Checkpoint and broker
writers then reject its lost job authority. The standalone command defaults to a
rollback-only plan; apply requires the unchanged plan fingerprint. Publication
failure rolls the reconciliation back too.

The local proofs use disposable PostgreSQL, stub services and browser fixtures.
The observed production job metadata is replayed locally, not a live database
copy. No paid calls, live database writes, merges or owner rebuild are performed.

The smoke gate also reproduced a clean-main regression: compactDecision hid the
action-consequence explanation permanently. Preserve that explanation alongside
the decision fields; do not weaken its desktop/phone accessibility check.

The wider shell matrix exposed a real interaction with the 18px floor: authenticated operator links no longer fit the old one-row header, and mobile sign-in exceeded the identity reservation. Keep the 18px floor. Reserve a second desktop navigation row and stable cost/identity slots rather than hide links after auth or truncate the credit balance. The existing delayed-auth, no-reload navigation, and signed-out geometry tests reproduce the regressions and now pass unchanged. Inspect both desktop and mobile pixels after this fix.
Delayed-font coverage also requires the mobile balance's row to have a fixed reservation. A content-sized balance moved when the real font replaced fallback metrics. Make it full-row and centered, matching mobile session cost. The delayed-font and delayed-auth assertions remain unchanged.
