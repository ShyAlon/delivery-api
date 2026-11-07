#!/usr/bin/env python3
"""Deterministic test-file classification for the fast PR gate.

Every tests/test_*.py file is either heavy or fast:
- heavy: needs disposable Postgres (pg_harness boots a server) or a real
  browser (Playwright). These run in the full matrix: workflow_dispatch,
  the nightly schedule, and ready_for_review.
- fast: everything else. These run in the every-PR gate.

Classification is content-based and deterministic so the gate and the matrix
can partition the suite without a hand-maintained list. A heavy token in a
fast test would either boot PG (slower, still correct) or fail on the missing
browser (loud); tests/test_ci_fast_gate.py pins the boundary with examples.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PG_TOKENS = (
    'pg_harness', 'DELIVERY_TEST_POSTGRES_DSN', 'test_postgres_store',
    'pg_task_app', 'pg_workspace_factory', 'PostgresStore', 'pg_workspace',
    'isolated_policy_database',
)
BROWSER_TOKENS = (
    'playwright', 'pg_browser', 'sync_playwright', 'async_playwright',
    'local_pg_signin', 'owner_browser_session', 'chromium',
)

def heavy_reason(path):
    src = path.read_text(errors='replace')
    for token in PG_TOKENS:
        if token in src:
            return 'pg:' + token
    for token in BROWSER_TOKENS:
        if token in src:
            return 'browser:' + token
    return None

def classify():
    fast, heavy = [], []
    for path in sorted((ROOT / 'tests').rglob('test_*.py')):
        reason = heavy_reason(path)
        (heavy if reason else fast).append(path.relative_to(ROOT).as_posix())
    return fast, heavy

def main(argv):
    mode = argv[1] if len(argv) > 1 else 'fast'
    fast, heavy = classify()
    if mode == 'fast':
        print('\n'.join(fast))
    elif mode == 'heavy':
        print('\n'.join(heavy))
    elif mode == 'summary':
        print(f'fast={len(fast)} heavy={len(heavy)} total={len(fast)+len(heavy)}')
    else:
        raise SystemExit('usage: classify_tests.py [fast|heavy|summary]')

if __name__ == '__main__':
    main(sys.argv)
