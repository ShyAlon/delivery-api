import pytest


def test_real_postgres_task_shape_keeps_canonical_key():
    import os,uuid
    dsn=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
    if not dsn:pytest.skip('isolated PG required')
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantScope,tenant_context
    store=PostgresStore(dsn.replace('user=sandbox','user=delivery_app'))
    scope=TenantScope('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1')
    with tenant_context(scope):
        task=store.create_task('phase-shape-'+uuid.uuid4().hex)
        try:
            store.update_phase(task['task_id'],'srs','approved')
            actual=store.task(task['task_id'])
            assert all('phase_key' in p and 'key' not in p for p in actual['phases'])
        finally:store.delete_task(task['task_id'])
