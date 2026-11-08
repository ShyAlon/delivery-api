# 2026-11-08 - pull-all said ok, then pytest and playwright were missing

## What happened
After a merge, pull-all.sh printed "syncing delivery-api ok" and minutes
later req migrate aborted with "missing in .venv: pytest". After the manual
repair, the preview browser smoke died raw with ModuleNotFoundError:
playwright. Two owner-step dependencies, both declared, both absent right
after a successful sync.

## Root cause
Owner-step and test dependencies live in each repo's dev
optional-dependency extra. pull-all.sh synced runtime dependencies only:
`uv sync --locked` without --extra dev, or the pip fallback
`pip install -e .` without .[dev]. venv_check.py caught pytest but never
listed playwright, so the smoke hit the second gap as a bare import error.

## Fix
pull-all.sh now detects a declared dev extra (inside
[project.optional-dependencies]) and installs it on every sync path:
`uv sync --extra dev` or `pip install -e .[dev]`. venv_check.py now gates
playwright too, so a future sync regression is named up front with the
exact install line instead of a traceback inside the smoke.

## Rule
If a dependency is required for a documented owner command, the standard
sync must install it. A partial sync that prints ok is worse than a failed
one: it moves the failure to a later, stranger place.
