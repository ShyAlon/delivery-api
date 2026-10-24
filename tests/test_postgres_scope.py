import os
import pytest
from delivery_api.postgres_scope import ScopedPostgres
from delivery_api.tenancy import TenantScope, tenant_context, TenantAccessDenied

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')


def test_superuser_credential_rejected():
    with pytest.raises(TenantAccessDenied): ScopedPostgres(DSN)


def test_transaction_identity_is_local_and_missing_scope_denied():
    import uuid
    from delivery_api.postgres_store import PostgresStore
    app_dsn=DSN.replace('user=sandbox','user=delivery_app')
    store=ScopedPostgres(app_dsn)
    # Each owner gets one task of their own; other tests leave their rows behind, so the check is "mine is visible, theirs is not", not "only mine exists".
    a_scope,b_scope=[TenantScope(owner,session) for owner,session in (('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1'),('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-0000000000a2'))]
    seeder=PostgresStore(app_dsn);made={}
    for scope in (a_scope,b_scope):
        with tenant_context(scope):made[scope.owner_id]=seeder.create_task('scope-'+uuid.uuid4().hex)['task_id']
    try:
        with pytest.raises(TenantAccessDenied):
            with store.transaction(): pass
        for scope,other in ((a_scope,b_scope),(b_scope,a_scope)):
            with tenant_context(scope),store.transaction() as connection:
                visible=[row['task_id'] for row in connection.execute('SELECT task_id FROM tasks')]
                assert made[scope.owner_id] in visible and made[other.owner_id] not in visible
        with pytest.raises(TenantAccessDenied):
            with store.transaction(): pass
    finally:
        for scope in (a_scope,b_scope):
            with tenant_context(scope):seeder.delete_task(made[scope.owner_id])
