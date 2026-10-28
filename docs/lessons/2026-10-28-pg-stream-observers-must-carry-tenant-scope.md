# PG stream observers must carry tenant scope

Async off-loop work inherits ContextVars through the production helper, but a raw
thread created by a test does not. When migrating commit-notification tests to
restricted PG, give that observer the actual workspace scope. Do not switch its
connection to admin or weaken its visibility assertion. The observer must still
see the committed row after the write notification, and stream reads must still
stay off the event-loop thread with their existing cadence bounds.
