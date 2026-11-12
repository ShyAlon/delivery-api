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


def test_isolated_acceptance_database_is_seeded_and_removed_after_failure():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    name=None
    with pytest.raises(RuntimeError,match='fixture failure'):
        with pg_harness.isolated_database() as dsn:
            name=conninfo_to_dict(dsn)['dbname']
            assert name!=conninfo_to_dict(pg_harness.STATE['dsn'])['dbname']
            with psycopg.connect(dsn) as connection:
                assert connection.execute('SELECT count(*) FROM auth.sessions').fetchone()[0]==2
                assert connection.execute('SELECT count(*) FROM credit_accounts').fetchone()[0]==0
                connection.execute("INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES('00000000-0000-0000-0000-000000000001',0,1000000000,0)")
            raise RuntimeError('fixture failure')
    with psycopg.connect(pg_harness.STATE['dsn']) as connection:
        assert connection.execute('SELECT count(*) FROM pg_database WHERE datname=%s',(name,)).fetchone()[0]==0
