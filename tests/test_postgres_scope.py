import os
import pytest
from delivery_api.postgres_scope import ScopedPostgres
from delivery_api.tenancy import TenantScope, tenant_context, TenantAccessDenied

DSN=os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark=pytest.mark.skipif(not DSN,reason='requires isolated migrated Postgres')


def test_superuser_credential_rejected():
    with pytest.raises(TenantAccessDenied): ScopedPostgres(DSN)


def test_transaction_identity_is_local_and_missing_scope_denied():
    app_dsn=DSN.replace('user=sandbox','user=delivery_app')
    store=ScopedPostgres(app_dsn)
    with pytest.raises(TenantAccessDenied):
        with store.transaction(): pass
    for owner,task in [('00000000-0000-0000-0000-000000000001','a'),
                       ('00000000-0000-0000-0000-000000000002','b')]:
        with tenant_context(TenantScope(owner,owner)),store.transaction() as connection:
            assert [row['task_id'] for row in connection.execute('SELECT task_id FROM tasks')] == [task]
    with pytest.raises(TenantAccessDenied):
        with store.transaction(): pass
