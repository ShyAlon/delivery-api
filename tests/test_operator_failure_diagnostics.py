"""The operator-only failure diagnostics store: capture works, validation
holds the presence-only contract, and no customer-visible store surface
(events, audit) ever carries the block - exclusion is structural, and these
tests pin it."""
import time

import pytest


BLOCK = {'error_class': 'InsufficientPrivilege', 'sqlstate': '42501', 'stage': 'session insert',
         'message': 'new row violates row-level security policy for table "[identifier]"',
         'role': 'delivery_app', 'sub_present': False, 'session_id_present': False,
         'session_active_for_sub': 'UNKNOWN'}


@pytest.fixture
def store(pg_scoped_workspace):
    return pg_scoped_workspace.store


def test_record_and_read_roundtrip(store):
    tid=__import__("uuid").uuid4().hex
    store.create_task('t1', task_id=tid)
    row_id = store.record_failure_diagnostics(tid, 'engine.start', 503, BLOCK)
    assert isinstance(row_id, int)
    rows = store.failure_diagnostics(tid)
    assert len(rows) == 1
    row = rows[0]
    assert row['source'] == 'engine.start' and row['http_status'] == 503
    assert row['sqlstate'] == '42501' and row['stage'] == 'session insert'
    assert row['sub_present'] == 'False' and row['session_active_for_sub'] == 'UNKNOWN'
    assert row['block'] == {**BLOCK}
    assert 'identifier' in row['message'] and 'engine_sessions' not in row['message']


def test_presence_contract_enforced(store):
    tid=__import__("uuid").uuid4().hex
    store.create_task('t1', task_id=tid)
    for key in ('sub_present', 'session_id_present', 'session_active_for_sub'):
        bad = {**BLOCK, key: 'yes'}
        with pytest.raises(ValueError):
            store.record_failure_diagnostics(tid, 'engine.start', 503, bad)
    with pytest.raises(ValueError):
        store.record_failure_diagnostics(tid, 'engine.start', 503, {**BLOCK, 'sqlstate': '4250'})
    with pytest.raises(ValueError):
        store.record_failure_diagnostics(tid, 'engine.start', 503, 'not-a-dict')


def test_no_customer_surface_carries_the_block(store):
    tid=__import__("uuid").uuid4().hex
    store.create_task('t1', task_id=tid)
    store.record_failure_diagnostics(tid, 'engine.start', 503, BLOCK)
    for event in store.events(tid):
        assert 'operator_diagnostics' not in str(event)
        assert 'InsufficientPrivilege' not in str(event)
    for entry in store.audit_entries(tid):
        assert 'InsufficientPrivilege' not in str(entry)


def test_bounded_page(store):
    tid=__import__("uuid").uuid4().hex
    store.create_task('t1', task_id=tid)
    with pytest.raises(ValueError):
        store.failure_diagnostics(tid, limit=0)


def test_diagnostics_fixture_uses_restricted_pg(store):
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    from pg_storage_guard import assert_pg_storage_only
    assert_pg_storage_only()
    with store._postgres.transaction() as connection:
        assert connection.execute('SELECT current_user').fetchone()['current_user']=='requi_backend'
