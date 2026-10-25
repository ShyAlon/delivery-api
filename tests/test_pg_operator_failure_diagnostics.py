"""The operator failure-diagnostics table on the real migrated schema, as the
real least-privilege roles: the backend writes only tenant-matching rows, the
customer role has no read at all (structural exclusion), and the reviewed
operator role reads under the existing logs.read capability."""
import os
import uuid

import pytest
import psycopg
import psycopg.rows

from delivery_api.postgres_store import PostgresStore
from delivery_api.tenancy import TenantScope, tenant_context

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
APP = DSN.replace('user=sandbox', 'user=delivery_app') if DSN else None
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
A = TenantScope('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1')
B = TenantScope('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-0000000000a2')

BLOCK = {'error_class': 'InsufficientPrivilege', 'sqlstate': '42501', 'stage': 'session insert',
         'message': 'row-level security policy for table "[identifier]"', 'role': 'delivery_app',
         'sub_present': False, 'session_id_present': False, 'session_active_for_sub': 'UNKNOWN'}


def _super():
    return psycopg.connect(DSN, autocommit=True, row_factory=psycopg.rows.dict_row)


def _app():
    return psycopg.connect(DSN.replace('user=sandbox', 'user=delivery_app'), autocommit=False,
                           row_factory=psycopg.rows.dict_row)


def _claims(conn, scope):
    conn.execute("SELECT set_config('request.jwt.claim.sub',%s,true),set_config('request.jwt.claim.session_id',%s,true)",
                 (scope.owner_id, scope.session_id))


@pytest.fixture(autouse=True)
def task_row():
    with _super() as conn:
        conn.execute("DELETE FROM public.operator_principals")
        conn.execute("INSERT INTO public.tasks(task_id,title,brief,state,model_route,generation,created_at,updated_at,owner_id)"
                     " VALUES('diag-task','t','','pending','route',1,1,1,%s) ON CONFLICT (task_id) DO NOTHING", (A.owner_id,))
    yield
    with _super() as conn:
        conn.execute("DELETE FROM public.operator_failure_diagnostics WHERE task_id='diag-task'")
        conn.execute("DELETE FROM public.tasks WHERE task_id='diag-task'")
        conn.execute("DELETE FROM public.operator_principals")


def test_backend_records_and_operator_reads_under_existing_capability():
    store = PostgresStore(DSN.replace('user=sandbox', 'user=delivery_app'))
    with tenant_context(A):
        row_id = store.record_failure_diagnostics('diag-task', 'engine.start', 503, BLOCK)
    assert isinstance(row_id, int)
    with _super() as conn:
        conn.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled)"
                     " VALUES(%s,'ops-v1',ARRAY['logs.read'],true)", (A.owner_id,))
    with _app() as conn:
        conn.execute('SET LOCAL ROLE requi_operator_read')
        _claims(conn, A)
        rows = conn.execute("SELECT sqlstate,stage,role,sub_present,session_active_for_sub,message FROM public.operator_failure_diagnostics"
                            " WHERE task_id='diag-task'").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row['sqlstate'] == '42501' and row['stage'] == 'session insert'
        assert row['sub_present'] == 'False' and row['session_active_for_sub'] == 'UNKNOWN'
        assert 'engine_sessions' not in row['message']


def test_backend_cannot_record_for_another_tenant():
    with _app() as conn:
        conn.execute('SET LOCAL ROLE requi_backend')
        _claims(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO public.operator_failure_diagnostics(owner_id,task_id,generation,source,http_status,error_class,"
                         "sqlstate,stage,role,sub_present,session_id_present,session_active_for_sub,message,block,created_at)"
                         " VALUES(%s,'diag-task',1,'engine.start',503,'X','42501','s','r','False','False','UNKNOWN','m','{}',1)",
                         (B.owner_id,))


def test_backend_select_is_owner_scoped():
    """INSERT ... RETURNING needs SELECT; the backend grant is owner-scoped,
    so tenant B's backend context must not see tenant A's diagnostics row."""
    store = PostgresStore(DSN.replace('user=sandbox', 'user=delivery_app'))
    with tenant_context(A):
        store.record_failure_diagnostics('diag-task', 'engine.start', 503, BLOCK)
    with _app() as conn:
        conn.execute('SET LOCAL ROLE requi_backend')
        _claims(conn, B)
        rows = conn.execute("SELECT id FROM public.operator_failure_diagnostics WHERE task_id='diag-task'").fetchall()
        assert rows == []


def test_customer_role_has_no_read_at_all():
    with _app() as conn:
        conn.execute('SET LOCAL ROLE authenticated')
        _claims(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT id FROM public.operator_failure_diagnostics LIMIT 1")


def test_non_operator_session_reads_nothing():
    with _super() as conn:
        conn.execute("INSERT INTO public.operator_principals(subject,registry_version,capabilities,enabled)"
                     " VALUES(%s,'ops-v1',ARRAY['logs.read'],true)", (A.owner_id,))
    with _app() as conn:
        conn.execute('SET LOCAL ROLE requi_operator_read')
        _claims(conn, B)
        rows = conn.execute("SELECT id FROM public.operator_failure_diagnostics").fetchall()
        assert rows == []


from test_cloud_api import cloud, header  # noqa: E402,F401


def test_route_rechecks_operator_authorization_and_serves_only_sanitized_rows(cloud, monkeypatch):
    """Realistic entrypoint verification through the HTTP app, not only the
    store: unauthenticated is 401, a signed-in non-operator is 404, a bad page
    bound is 422, the enabled operator under the existing logs.read capability
    gets the sanitized rows, and disabling the principal closes the route."""
    import psycopg
    import delivery_api.app as module
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    client, store, _ = cloud
    with tenant_context(A):
        store.record_failure_diagnostics('diag-task', 'engine.start', 503, BLOCK)
    with psycopg.connect(DSN) as c:
        c.execute('GRANT requi_operator_read TO delivery_app')
        c.execute("INSERT INTO operator_principals VALUES (%s,%s,%s,true)",
                  (A.owner_id, 'route-diag', ['logs.read']))
    monkeypatch.setattr(module.app.state, 'operator_read_store',
                        OperatorReadStore(APP, OperatorPrincipal(A.owner_id, 'route-diag', frozenset({'logs.read'}))),
                        raising=False)
    path = '/api/operator/task-failure-diagnostics?task_id=diag-task'
    try:
        assert client.get(path).status_code == 401
        got = client.get(path, headers=header('a'))
        assert got.status_code == 200
        body = got.json()
        assert body['content'] == 'sanitized_failure_diagnostics_only'
        assert body['mutations_available'] is False
        assert len(body['diagnostics']) == 1
        row = body['diagnostics'][0]
        assert row['error_class'] == 'InsufficientPrivilege' and row['sqlstate'] == '42501'
        assert row['stage'] == 'session insert' and row['http_status'] == 503
        assert row['sub_present'] == 'False' and row['session_active_for_sub'] == 'UNKNOWN'
        assert 'engine_sessions' not in row['message']
        assert client.get(path, headers=header('b')).status_code == 404
        assert client.get(path + '&limit=0', headers=header('a')).status_code == 422
        assert client.get(path + '&limit=1000', headers=header('a')).status_code == 422
        with psycopg.connect(DSN) as c:
            c.execute('UPDATE operator_principals SET enabled=false WHERE subject=%s', (A.owner_id,))
        assert client.get(path, headers=header('a')).status_code == 404
    finally:
        with psycopg.connect(DSN) as c:
            c.execute('DELETE FROM operator_principals WHERE subject=%s', (A.owner_id,))
