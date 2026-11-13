"""The preview launcher's venv gate: the steps ahead (the startup-preflight
tests, the pin) import pytest, psycopg and psycopg_pool from this checkout's
.venv. A venv that predates the pool redesign has no psycopg_pool
(2026-10-27: the launcher died inside a step-2 pytest import traceback
instead of saying so). ops_venv refreshes an old venv by itself since the
extras fix; this gate is the belt for anything the refresh could not cover.
The pin also imports the vendored child and model contracts. A working pool
alone does not prove an older venv has these newer dependencies.
Exit 1 names the exact per-module install line."""
import importlib.util
import sys

MODULES = {'psycopg': 'psycopg[binary]>=3.2,<4', 'psycopg_pool': 'psycopg-pool>=3.2,<4', 'pytest': 'pytest>=8',
           # The preview browser smoke imports playwright from this venv on the
           # host; without it the smoke dies raw instead of this gate speaking.
           'playwright': 'playwright>=1.48',
           'requi_child_contract': 'vendor/requi_child_contract-0.3.0-py3-none-any.whl',
           'vmodel_contracts': 'vendor/vmodel_contracts-0.29.0-py3-none-any.whl'}

missing = [(module, package) for module, package in MODULES.items() if importlib.util.find_spec(module) is None]
if missing:
    print('missing in .venv: ' + ', '.join(module for module, _ in missing))
    print('install exactly: .venv/bin/python -m pip install ' + ' '.join("'%s'" % package for _, package in missing))
    sys.exit(1)
print('venv deps: ' + ', '.join(MODULES) + ' all present')
