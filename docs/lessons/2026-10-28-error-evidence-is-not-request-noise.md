# Error evidence is not successful request noise

Generic endpoint polling lines do not explain a failure. Keep failed/slow
requests and warning/error evidence, with a request/task correlation field.
Use a restart-readable bounded diagnostic file, not a new business DB table.
Database driver messages can echo parameter values: emit SQLSTATE, error class
and a code-owned cause instead, never query text or parameters. Stack frames
carry filename/function/line only, not source text or exception values.

This first stream is capped at32 MiB. Existing activity/stage/engine logs retain
their own bounds until the later log-budget consolidation theme. File retention
is not guaranteed across container replacement without a persisted volume.
