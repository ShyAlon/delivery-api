# 2026-11-10: `req preview --apply` told the owner to stop a container by hand

Problem: after pulling main, `req image --accept-local && req preview --apply` failed with "preview container already
exists and is running; ... docker stop requi-preview-exec". The documented fallback skipped the stop-and-rename that only
`req rebuild --apply` (step_preview_restart) performed, so every use of the fallback after a first preview hit it.
Owner rule: never make him stop containers manually.

Fix: preview --apply, after the owner's typed yes (the prompt names the replacement), stops a running requi-preview-exec
(graceful, 180 s), renames it to requi-preview-exec-<timestamp> and starts the accepted image. An exited one is only
renamed. Nothing is removed or force-killed; the old container, its logs and its image stay for rollback and the rebuild
cleanup step reviews them. A missing container starts as before.

Sibling not changed: local_pg_preview.py ("owned local app already exists; use explicit stop before restarting") has the
same shape in the --local-pg mode, which has persistent PG data; separate decision.

Rule: a fallback path must do what the main path does for the state it leaves behind.
