# 2026-11-08 - The rebuild ritual existed only in the owner's head, and its sync step lied by omission

## What happened
Rebuilding the owner preview after a merge was a hand-typed seven-command
sequence: req status, pull-all.sh, req image --review, req image
--accept-local, req migrate review/apply, stop+rename the old preview
container, req preview --apply, then manual cleanup of renamed containers and
old images. On 2026-11-08 the ritual broke mid-sequence: pull-all.sh printed
"syncing delivery-api ok" and req migrate immediately aborted with "missing
in .venv: pytest".

## Root cause
pull-all.sh syncs runtime dependencies only: `uv sync --locked` without
--extra dev, or the pip fallback `pip install -e .` without .[dev]. pytest,
psycopg and the vendored contract wheels live in the dev optional-dependency
extra (pyproject [project.optional-dependencies] dev), so a fully successful
sync can leave the owner-step dependencies absent. The venv gate in
req-owner.sh (scripts/ops/venv_check.py) did its job and named the exact
repair line, but it fires when the first owner command runs, not when the
sync claims success.

## Fix
req rebuild runs the whole sequence as one owner command: dry plan review by
default, --apply to run, per-step resume keyed by the six origin/main
revisions plus the runtime and acceptance contracts, and a state file under
~/.local/share/requi/rebuild/. Its preflight runs venv_check.py FIRST, before
any fetch or build, so the missing-dev-extra class fails fast with the exact
per-module install line. Every typed confirmation inside req image
--accept-local, req migrate --apply and req preview --apply is still answered
by the owner at the terminal; the orchestrator inherits stdio and never feeds
a confirmation. Cleanup keeps the running preview and the newest renamed
container (and their images) and deletes the rest only after an exact typed
"DELETE n CONTAINERS AND m IMAGES".

## Rule
A sync that prints ok must cover every dependency the next command imports,
or the gate that catches the gap must run before the sync's claim, not after.
A repeated manual ritual is a script waiting to be written; when it is
written, its confirmations stay the owner's.
