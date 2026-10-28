"""The throwaway Postgres behind the database tests must be up when it is supposed to be, and loud when it is not."""
import os

import pytest

import pg_harness


def test_the_temporary_database_started_or_was_deliberately_off():
    state=pg_harness.STATE
    if state['status'] in ('off','missing','external'):pytest.skip(state['reason'])
    assert state['status']=='running',state['reason']   # a failed start must never silently turn 250 database tests into skips


def test_every_migration_was_applied_and_the_tests_can_connect():
    state=pg_harness.STATE
    if state['status']!='running':pytest.skip(state['reason'] or state['status'])
    import psycopg
    files=sorted(p.name for p in pg_harness.MIGRATIONS.glob('*.sql') if pg_harness._numbered(p))
    assert state['applied']==files and files
    assert os.environ['DELIVERY_TEST_POSTGRES_DSN']==state['dsn']
    with psycopg.connect(state['dsn']) as c:
        assert c.execute("SELECT to_regclass('public.credit_accounts') IS NOT NULL").fetchone()[0]
        assert c.execute("SELECT count(*) FROM auth.sessions").fetchone()[0]>=2
    with psycopg.connect(state['dsn'].replace('user=sandbox','user=delivery_app')) as c:
        assert c.execute('SELECT current_user').fetchone()[0]=='delivery_app'


def test_repeated_start_preserves_owned_server_for_warm_runs():
    if pg_harness.STATE['status'] != 'running':
        pytest.skip('requires the disposable harness')
    before = dict(pg_harness.STATE)
    pg_harness.start()
    assert pg_harness.STATE == before
