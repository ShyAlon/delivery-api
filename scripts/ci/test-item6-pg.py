#!/usr/bin/env python3
"""Run item6 connected tests on an owned disposable server, never a supplied DSN.

Run from a delivery-api checkout with interview-engine and spec-generator
installed (or supplied through PYTHONPATH). No provider calls or real payment.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'src'), str(ROOT)]
for key in ('DELIVERY_TEST_POSTGRES_DSN', 'ITEM6_TEST_ADMIN_DSN', 'PAYMENT_TEST_ADMIN_DSN'):
    if os.environ.get(key):
        raise SystemExit('Refusing supplied database: ' + key)

import importlib.util
for package,key in (('spec_generator','DELIVERY_TEST_SPEC_SOURCE'),
                    ('interview_engine','DELIVERY_TEST_ENGINE_SOURCE')):
    found=importlib.util.find_spec(package)
    if found is None or found.origin is None:
        raise SystemExit('Install sibling source checkout: '+package)
    source=Path(found.origin).resolve().parents[1]
    if not (source.parent/'tests').is_dir():
        raise SystemExit('Sibling source tests required: '+package)
    os.environ.setdefault(key,str(source if package=='spec_generator' else source.parent))
os.environ.setdefault('DELIVERY_TEST_GENERATOR_SOURCE',str(Path(os.environ['DELIVERY_TEST_SPEC_SOURCE']).parent))

import pg_harness
pg_harness.start(database_prefix='requi_item6_', socket_directory=Path('/tmp'))
if pg_harness.STATE['status'] != 'running':
    raise SystemExit('Disposable database unavailable: ' + str(pg_harness.STATE))
import psycopg
with psycopg.connect(pg_harness.STATE['dsn'], autocommit=True) as connection:
    connection.execute('CREATE ROLE delivery_payment_test LOGIN NOSUPERUSER NOBYPASSRLS')
    connection.execute('GRANT requi_payment TO delivery_payment_test')
os.environ['ITEM6_TEST_ADMIN_DSN'] = pg_harness.STATE['dsn']
import pytest
raise SystemExit(pytest.main(['-q', '--timeout=90', '--tb=short',
    'tests/test_item6_connected_pg.py', 'tests/test_item6_connected_graph_pg.py',
    'tests/test_item6_local_auth.py', *sys.argv[1:]]))
