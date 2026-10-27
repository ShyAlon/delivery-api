"""The preview launcher's venv gate: the steps ahead (the startup-preflight
tests, the pin) import pytest, psycopg and psycopg_pool from this checkout's
.venv. A venv that predates the pool redesign has no psycopg_pool
(2026-10-27: the launcher died inside a step-2 pytest import traceback
instead of saying so). Exit 1 names the exact per-module install line."""
import importlib.util
import sys

MODULES = {'psycopg': 'psycopg[binary]>=3.2,<4', 'psycopg_pool': 'psycopg-pool>=3.2,<4', 'pytest': 'pytest>=8'}

missing = [(module, package) for module, package in MODULES.items() if importlib.util.find_spec(module) is None]
if missing:
    print('missing in .venv: ' + ', '.join(module for module, _ in missing))
    print('install exactly: .venv/bin/python -m pip install ' + ' '.join("'%s'" % package for _, package in missing))
    sys.exit(1)
print('venv deps: ' + ', '.join(MODULES) + ' all present')
