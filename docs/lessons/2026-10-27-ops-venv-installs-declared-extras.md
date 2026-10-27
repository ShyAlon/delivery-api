# 2026-10-27 - ops_venv installs the declared extras and refreshes old venvs

The ops venv is built once and then trusted forever: `pip install -e .` with no
extras, and `[ -x .venv/bin/python ] && return 0` for every later run. A merged
redesign that adds a dependency declared only in extras (psycopg-pool, for the
process pool) breaks every existing venv at once, and the failure shows up far
from the cause.

Rules:
- The venv builder installs what the repo declares the tools need:
  `pip install -e '.[dev,supabase]'` (his choice, 2026-10-27).
- An existing venv is probed (import psycopg_pool) and refreshed in place; a
  failed refresh aborts WITHOUT deleting the old venv - it still works for
  everything that does not need the extras.
- The step-1 venv gate in preview-exec (venv_check.py) stays as the belt.
