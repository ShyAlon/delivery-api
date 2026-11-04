# Measure empty progress cost and keep acceptance accounting explainable

The successful owner run showed113.669s answer wall time:4.575s validation,
99.903s synchronous child submit,7.031s final progress drain and2.158s response
writes.430DB queries/114.181s aggregate DB time overlap, so they are not an
exclusive critical-path split. Cheap provider expense does not imply fast HTTP.

Instrument before changing: on disposable owned PostgreSQL the original empty
poll cost5delivery queries/2transactions. The same fake source after removing
only empty-page ingestion costs2queries/1transaction. Source engine queries are
excluded equally in both. This is3fewer queries and1fewer transaction per empty
poll, not a promised wall-time improvement. The cursor SELECT now also requires
the current owned nondeleted task; revocation/foreign task checks still fail.
Nonempty pages retain atomic gap checks, event dedupe and rollback behavior.
Empty source pages may not advance cursor. Poll cadence, source RLS, child
liveness checks, Stop fencing, accounting and publication remain unchanged.

The owner run's3522tokens at0.0000125credits each exactly explain0.044025 token
credits. The displayed0.046647 includes compute too. Its0.002622difference
corresponds to52440ms at the default compute rate, inferred from rounded price,
not independently confirmed by that console (no compute_ms was printed).
Provider peak/cache rates do not set customer retail. Provider charge groups
exclude compute, while the actual account is debited for both contributions.

Acceptance now reads an owner-test-only compact breakdown before purge and
writes it privately first. Token/compute exact and posted units, actual compute
milliseconds, child setup/supervisor/completion/slot timings, provider HTTP vs
journal/settlement timings and request timings are numeric allowlists. No
prompt/answer/raw provider response/credentials are exported. DB journal windows
include admission/persistence and are not mislabeled pure provider HTTP time.
Old children lacking new timings remain explicitly empty, not invented zeros.
The script refuses missing or mismatched accounting/child evidence and keeps
content if the read or file write fails. Existing purge guards still decide
whether cleanup is safe. No migration, live DB write, deploy or paid run here.
