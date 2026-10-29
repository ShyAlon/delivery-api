"""Explicit trusted-call context for MockTransport engine unit tests."""
import pytest
from delivery_api.tenancy import TenantScope,tenant_context


@pytest.fixture
def engine_scope():
    # No persistence or identity-verification claim: these tests isolate the
    # HTTP client's serialization/error/timing contract after authentication.
    with tenant_context(TenantScope('00000000-0000-0000-0000-000000000001',
                                   '00000000-0000-0000-0000-0000000000a1')):
        yield
