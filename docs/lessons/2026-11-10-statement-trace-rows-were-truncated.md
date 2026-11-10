# 2026-11-10: the statement trace logged 5 rows of an 845-round-trip answer

Problem: db.statement_trace joined up to 30 ranked rows into one `rows` field. The log
keeps 500 characters per field, so everything after the fifth row was "[truncated]" and
about 600 of 845 round trips could not be ranked from the debug ZIP.

Fix: a header line (request type, round trips) plus one `db.statement_row` line per
ranked row (rank, count, ms, purpose, shape), each under the field cap.

Rule: a diagnostic that exists to be read from a ZIP must be checked for the log's field
cap with a realistic row count, not 2 rows.
