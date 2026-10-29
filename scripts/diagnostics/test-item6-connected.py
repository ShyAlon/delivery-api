#!/usr/bin/env python3
"""Run the two hermetic item6 connected checks in an owned disposable PG.

Use the project's Python with pgserver/psycopg and the engine/generator installed.
No external database, PayPal request, model call, or deployed process is used.
The exact /tmp socket is required by these tests. Refuse an occupied socket;
pg_harness owns the new server and its normal guardian/exit cleanup.
A graph failure (including checkpoint_ns) is returned unchanged, never skipped.
"""
import os
import importlib.util
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
TESTS = [
    'tests/test_item6_connected_pg.py',
    'tests/test_item6_connected_graph_pg.py',
]


def main():
    forbidden = [name for name in (
        'DELIVERY_TEST_POSTGRES_DSN', 'DELIVERY_TEST_NO_POSTGRES',
        'ITEM6_TEST_ADMIN_DSN', 'PAYMENT_TEST_ADMIN_DSN', 'PAYMENT_TEST_POSTGRES_DSN',
    ) if os.environ.get(name)]
    if forbidden:
        raise SystemExit('Refusing preconfigured database controls: ' + ', '.join(forbidden))
    for name in ('.s.PGSQL.5432', '.s.PGSQL.5432.lock'):
        if (Path('/tmp') / name).exists():
            raise SystemExit('Refusing an existing /tmp PostgreSQL socket or lock')
    sources = {}
    for module in ('interview_engine', 'spec_generator'):
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.origin:
            raise SystemExit('Required source package not installed: ' + module)
        sources[module] = Path(spec.origin).resolve().parents[1]
    generator_source = Path(os.getenv('DELIVERY_TEST_SPEC_SOURCE', sources['spec_generator']))
    if not (generator_source.parent / 'tests/conftest.py').is_file():
        raise SystemExit('Generator test-source checkout required via DELIVERY_TEST_SPEC_SOURCE=/checkout/src')
    # Only the server-location chooser is configured. No test or safety guard is
    # changed. pgserver binds no TCP address, and its server is owned by pg_harness.
    import pgserver.postgres_server as pgmod
    pgmod.find_suitable_socket_dir = lambda *args: Path('/tmp')
    sys.path.insert(0, str(ROOT / 'tests'))
    import pg_harness
    pg_harness.start()
    if pg_harness.STATE['status'] != 'running':
        raise SystemExit('Owned PG setup failed: ' + pg_harness.STATE['reason'])
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    dsn = pg_harness.STATE['dsn']
    info = conninfo_to_dict(dsn)
    if info['host'] != '/tmp' or not info['dbname'].startswith('test_tenant_'):
        raise SystemExit('Owned PG did not match the expected fixture namespace')
    name = 'requi_item6_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute('CREATE ROLE delivery_payment_test LOGIN NOSUPERUSER NOBYPASSRLS')
        conn.execute('GRANT requi_payment TO delivery_payment_test')
        conn.execute(psycopg.sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(
            psycopg.sql.Identifier(name), psycopg.sql.Identifier(info['dbname'])))
    env = os.environ | {
        'DELIVERY_TEST_SPEC_SOURCE': str(generator_source),
        'ITEM6_TEST_ADMIN_DSN': make_conninfo(**(info | {'dbname': name})),
        'PYTHONPATH': os.pathsep.join(filter(None, [str(ROOT), str(ROOT / 'src'), os.getenv('PYTHONPATH')])),
    }
    print('Owned migrated fixture ready, exact /tmp socket; running both checks', flush=True)
    os.environ.update(env)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'src'))
    import pytest
    return int(pytest.main(['-q', '--tb=short', *TESTS]))


if __name__ == '__main__':
    raise SystemExit(main())
