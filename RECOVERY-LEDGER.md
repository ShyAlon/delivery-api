# Recovery ledger

Not merge-ready. New commits preserve recovered source, never recreate original Git objects.

| Unit | Recovery source | Fresh validation | Remaining validation |
| --- | --- | --- | --- |
| Four wheel-source bases | Hash-verified v3 wheels, source-manifest.json | Original wheel hashes verified; initial remote tests reveal tracked test/API mismatches | Reconcile tracked tests/build/migrations to WIP provenance; full hermetic suites |
| Descriptor-relative producer containment (lost1c4a4c7) | Own archived source-edit tool records Oct9 11:14:04 and11:14:22; original reader/tests at11:13:36 |9 targeted tests passed on reconstructed source | Disposable Postgres regression not rerun yet; no atomic multidocument snapshot claim |
| Suspension pure classifier/proposal (lost65b3a33) | Archived source write Oct9 11:17:18, unused-import cleanup11:17:25 |40 targeted tests passed on reconstructed source | Operator effect remains unavailable; state/enforcement units not yet reapplied |
| Producer same-inode/cross-document content stamps (lostf1d50d2) | Exact source-edit script archived Oct9 12:32:28 |11 targeted tests passed on reconstructed source | Postgres/broad not rerun; observed-mutation detector, not atomic snapshot |
| Immutable retained reviews/reused first gate snapshot (lost4760507) | Exact source edits Oct9 12:13:03,12:13:57,12:14:29,12:15:36, original replay test + gate regression restored |11 related local tests passed; existing warning | Postgres replay regression pending original migrated fixture recovery |
| Same engine revision evidence conflict409 (lost8ea154e) | Exact source/test edits Oct9 12:33:50 |12 related local tests passed; existing warning | Full-suite asset recovery pending |
| Closed revision preflight/completeness (lostd7d8719+8394804), baseline draft tests restored | Exact11:51:56 and11:55:01 source/test writes, early gate tests from06:47/06:53/07:59 corrected helper evidence |25 related tests passed; existing warning | Non-atomic prep only, no commit/restart route |
| Regenerate edited phase before descendants + explicit cost review (lost8130cfd) | Exact source/test script Oct9 12:19:37 |23 related tests passed; existing warning | No commit/dispatch, amount unknown; per-call admission and cost acknowledgement still required |
| Phase-specific regeneration capability map (lost39596f4) | Exact source/test edits Oct9 12:31:11 |32 related tests passed; existing warning | Closed adapters unavailable; needs requires upstream IntentModel revision, plain rerun cannot consume prose |
