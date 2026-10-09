# Recovery ledger

Not merge-ready. New commits preserve recovered source, never recreate original Git objects.

| Unit | Recovery source | Fresh validation | Remaining validation |
| --- | --- | --- | --- |
| Four wheel-source bases | Hash-verified v3 wheels, source-manifest.json | Original wheel hashes verified; initial remote tests reveal tracked test/API mismatches | Reconcile tracked tests/build/migrations to WIP provenance; full hermetic suites |
| Descriptor-relative producer containment (lost1c4a4c7) | Own archived source-edit tool records Oct9 11:14:04 and11:14:22; original reader/tests at11:13:36 |9 targeted tests passed on reconstructed source | Disposable Postgres regression not rerun yet; no atomic multidocument snapshot claim |
| Suspension pure classifier/proposal (lost65b3a33) | Archived source write Oct9 11:17:18, unused-import cleanup11:17:25 |40 targeted tests passed on reconstructed source | Operator effect remains unavailable; state/enforcement units not yet reapplied |
| Producer same-inode/cross-document content stamps (lostf1d50d2) | Exact source-edit script archived Oct9 12:32:28 |11 targeted tests passed on reconstructed source | Postgres/broad not rerun; observed-mutation detector, not atomic snapshot |
| Immutable retained reviews/reused first gate snapshot (lost4760507) | Exact source edits Oct9 12:13:03,12:13:57,12:14:29,12:15:36, original replay test + gate regression restored |11 related local tests passed; existing warning | Postgres replay regression pending original migrated fixture recovery |
