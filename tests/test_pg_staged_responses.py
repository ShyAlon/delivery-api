"""PR B staged_provider_responses schema contract against the REAL migration chain
(pg_harness applies every numbered migration): tenant isolation (no leak, no oracle),
immutability (no UPDATE/DELETE), loud conflict on same-key different payload."""
import os
import subprocess
import pytest

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'


def sql(query, owner=A, success=True):
    prefix = f"BEGIN; SET LOCAL ROLE requi_backend; SELECT set_config('request.jwt.claim.sub','{owner}',true); SELECT set_config('request.jwt.claim.session_id','{owner}',true); "
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq', '-f', '-'],
                            input=prefix + query + '; ROLLBACK;', text=True, capture_output=True)
    if success:
        assert result.returncode == 0, result.stderr
    return result


def insert(owner, response='{"v": 1}'):
    return (f"INSERT INTO staged_provider_responses(owner_id,session_id,stage,item,request_sha256,response,provider,model,created_at) "
            f"VALUES ('{owner}','sess-{owner[-1]}','scope',NULL,'sha-{owner[-1]}','{response}','fake','m',1)")


def test_tenant_isolated_no_leak_no_oracle():
    assert sql(f"{insert(A)}; SELECT count(*) FROM staged_provider_responses WHERE request_sha256='sha-{A[-1]}'").stdout.strip().splitlines()[-1] == '1'
    # B cannot see A's staged row at all.
    assert sql(f"{insert(B)}; SELECT count(*) FROM staged_provider_responses", owner=B).stdout.strip().splitlines()[-1] == '1'


def test_immutable_no_update_no_delete():
    r = sql(f"{insert(A)}; UPDATE staged_provider_responses SET response='{{\"v\": 2}}'", success=False)
    assert r.returncode != 0 and 'permission denied' in r.stderr
    r = sql(f"{insert(A)}; DELETE FROM staged_provider_responses", success=False)
    assert r.returncode != 0 and 'permission denied' in r.stderr


def test_duplicate_key_conflicts_loudly():
    r = sql(insert(A) + '; ' + insert(A, '{"v": 2}'), success=False)
    assert r.returncode != 0 and 'duplicate key' in r.stderr
