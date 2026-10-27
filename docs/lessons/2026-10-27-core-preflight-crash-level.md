# Preserve startup check safety levels on unexpected errors

Problem: the runner promised that a core check exception would refuse startup,
but its generic handler made every exception a warning. An unverified artifacts
root, database/claims, required tables or engine could therefore allow startup.

Keep the failure policy beside each builder, before evaluation can throw. Core
exceptions become refuse/fail; optional checks remain warn/warn, including
feature-table checks. Continue the report and close acquired connections. Do not
copy unexpected exception messages into the report: they may contain credentials
or user data. The named check, exception category and source location are enough
for this fallback; existing expected failure diagnostics remain unchanged.

Regression coverage injects an exception into all nine builders, checks report
completion/connection cleanup, serialized status and text, main exit codes and
report persistence, and verifies a fake credential-bearing exception is hidden.
Tests were run failing first. No live database or provider call was needed.
