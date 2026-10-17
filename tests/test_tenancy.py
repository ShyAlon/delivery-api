from concurrent.futures import ThreadPoolExecutor
import asyncio
import pytest
from delivery_api.tenancy import (
    TenantScope, TenantAccessDenied, tenant_context, current_scope,
    session_root, contained_file,
)

A = TenantScope('00000000-0000-0000-0000-000000000001')
B = TenantScope('00000000-0000-0000-0000-000000000002')


def test_scope_missing_and_restored():
    with pytest.raises(TenantAccessDenied): current_scope()
    with tenant_context(A):
        assert current_scope() == A
        with tenant_context(B): assert current_scope() == B
        assert current_scope() == A
    with pytest.raises(TenantAccessDenied): current_scope()


def test_exception_restores_scope():
    with pytest.raises(RuntimeError):
        with tenant_context(A): raise RuntimeError('failure')
    with pytest.raises(TenantAccessDenied): current_scope()


def test_concurrent_async_requests_are_isolated():
    async def task(scope):
        with tenant_context(scope):
            await asyncio.sleep(0)
            return current_scope()
    async def run(): return await asyncio.gather(task(A), task(B))
    assert asyncio.run(run()) == [A, B]


def test_workers_do_not_inherit_accidental_identity():
    with tenant_context(A), ThreadPoolExecutor(1) as pool:
        with pytest.raises(TenantAccessDenied): pool.submit(current_scope).result()
        def scoped_worker():
            with tenant_context(A): return current_scope()
        assert pool.submit(scoped_worker).result() == A


@pytest.mark.parametrize('value', ['', 'local-admin', '../x', None])
def test_invalid_identity(value):
    with pytest.raises(TenantAccessDenied): TenantScope(value)


def test_same_session_has_distinct_tenant_namespace(tmp_path):
    a = session_root(tmp_path, A, 'session1')
    b = session_root(tmp_path, B, 'session1')
    assert a != b
    assert a == tmp_path / 'tenants' / A.owner_id / 'sessions' / 'session1'


@pytest.mark.parametrize('value', ['../x', '.', '..', '/tmp/x', 'a/b', 'a.b', ''])
def test_session_traversal_rejected(tmp_path, value):
    with pytest.raises(TenantAccessDenied): session_root(tmp_path, A, value)


def test_namespace_symlink_rejected(tmp_path):
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'tenants').symlink_to(tmp_path / 'outside')
    with pytest.raises(TenantAccessDenied): session_root(tmp_path, A, 'session1')


def test_artifact_escape_and_cross_tenant_symlink_rejected(tmp_path):
    a = session_root(tmp_path, A, 's')
    b = session_root(tmp_path, B, 's')
    a.mkdir(parents=True); b.mkdir(parents=True)
    (b / 'secret.json').write_text('private')
    (a / 'escape').symlink_to(b, target_is_directory=True)
    for value in ['../x', '/tmp/x', 'escape/secret.json']:
        with pytest.raises(TenantAccessDenied): contained_file(a, value)
    assert contained_file(a, 'spec/architecture.json') == a / 'spec' / 'architecture.json'
