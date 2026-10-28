# Budget global logs and bind worker evidence

A32MiB diagnostic stream added beside uncounted logs is not a32MiB managed-log
budget. Name each global stream and its rotations, including all eight stage
logs. Keep per-run audit artifacts separately labelled; changing them could
change accounting/debug provenance. Bounds do not imply historical recovery.

Task/job/session context belongs at worker execution boundaries. Do not add a DB
read just to label a log: bind the already-authorized context and preserve cause
when the worker fails. A buffer/tee failure must leave a safe degradation warning
rather than disappear. No log message needs credentials or arbitrary query values.
