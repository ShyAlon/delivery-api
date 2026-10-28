# Dedupe fixtures use distinct PG request transactions

Two adapters sharing a Python thread do not prove concurrent request dedupe.
Use separate PG stores and real request transaction handles in the existing
barrier-based helper;assert both stale snapshots resolve to the same durable
row. Preserve duplicate identity and original data past the first event page.
Translate SQLite connection transaction state to PG operation release and a
successful subsequent write,without weakening the original rollback assertion.
