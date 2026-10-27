# 2026-10-27 - the launcher's venv is a step-1 input, not a step-2 surprise

A dependency added by a merged redesign (psycopg-pool, for the process pool)
broke the preview launcher one step later than it should have: the venv was
fine for every old step, so the first thing to trip was the step-2 pytest run,
which died inside conftest's import chain with a bare ModuleNotFoundError.

Rules:
- If a launcher step runs pytest or imports project code, the venv's declared
  dependencies are an INPUT of the run: check them in the inputs step with a
  cheap find_spec probe (scripts/ops/venv_check.py) and abort naming the exact
  per-module pip install line.
- pyproject extras that the launcher needs must also be installed by the venv
  builder: ops_venv installs `-e .` without extras, so a fresh ops venv lacks
  psycopg-pool too. Until that is revisited, the gate names the exact fix.
