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
    return (f"INSERT INTO staged_provider_responses(owner_id,session_id,generation,stage,item,ordinal,request_sha256,response,provider,model,dispatched_at) "
            f"VALUES ('{owner}','sess-{owner[-1]}',1,'scope','',0,'sha-{owner[-1]}','{response}','fake','m',1)")


def test_tenant_isolated_no_leak_no_oracle():
    assert sql(f"{insert(A)}; SELECT count(*) FROM staged_provider_responses WHERE request_sha256='sha-{A[-1]}'").stdout.strip().splitlines()[-1] == '1'
    # B cannot see A's staged row at all.
    assert sql(f"{insert(B)}; SELECT count(*) FROM staged_provider_responses", owner=B).stdout.strip().splitlines()[-1] == '1'


def test_no_delete_grant():
    r = sql(f"{insert(A)}; DELETE FROM staged_provider_responses", success=False)
    assert r.returncode != 0 and 'permission denied' in r.stderr


def test_duplicate_key_conflicts_loudly():
    r = sql(insert(A) + '; ' + insert(A, '{"v": 2}'), success=False)
    assert r.returncode != 0 and 'duplicate key' in r.stderr


def insert_dispatched(owner):
    # A dispatched-but-incomplete row (response NULL): the crash-evidence state.
    return (f"INSERT INTO staged_provider_responses(owner_id,session_id,generation,stage,item,ordinal,request_sha256,provider,model,dispatched_at) "
            f"VALUES ('{owner}','sess-{owner[-1]}',1,'scope','',0,'sha-{owner[-1]}','fake','m',1)")


def complete(owner, usage='{"input_tokens": 5}'):
    return (f"UPDATE staged_provider_responses SET response='{{\"v\": 1}}', usage='{usage}', completed_at=2"
            f" WHERE session_id='sess-{owner[-1]}'")


def test_trigger_completed_response_is_immutable():
    seed = insert_dispatched(A) + '; ' + complete(A)
    r = sql(seed + "; UPDATE staged_provider_responses SET response='{\"v\": 2}' WHERE session_id='sess-1'", success=False)
    assert r.returncode != 0 and 'completed response is immutable' in r.stderr, r.stderr
    r = sql(seed + "; UPDATE staged_provider_responses SET usage='{\"input_tokens\": 9}' WHERE session_id='sess-1'", success=False)
    assert r.returncode != 0 and 'completed response is immutable' in r.stderr, r.stderr


def test_trigger_identity_fields_are_immutable():
    seed = insert_dispatched(A) + '; ' + complete(A)
    for col, val in (("request_sha256", "'forged'"), ("provider", "'forged'"), ("stage", "'forged'"),
                     ("execution_point", "'forged'"), ("dispatched_at", "9")):
        r = sql(seed + f"; UPDATE staged_provider_responses SET {col}={val} WHERE session_id='sess-1'", success=False)
        assert r.returncode != 0 and 'identity fields are immutable' in r.stderr, (col, r.stderr)


def test_trigger_attempt_binding_is_immutable():
    seed = insert_dispatched(A) + "; UPDATE staged_provider_responses SET attempt_id='attempt-1' WHERE session_id='sess-1'"
    r = sql(seed + "; UPDATE staged_provider_responses SET attempt_id='attempt-2' WHERE session_id='sess-1'", success=False)
    assert r.returncode != 0 and 'attempt binding is immutable' in r.stderr, r.stderr


def test_trigger_lifecycle_transitions_still_work():
    # dispatch -> bind -> complete: the two legitimate transitions pass the guard.
    seed = (insert_dispatched(A)
            + "; UPDATE staged_provider_responses SET attempt_id='attempt-1' WHERE session_id='sess-1'"
            + '; ' + complete(A))
    out = sql(seed + "; SELECT count(*) FROM staged_provider_responses WHERE session_id='sess-1' AND response IS NOT NULL")
    assert out.stdout.strip().splitlines()[-1] == '1'


def test_trigger_completion_requires_completed_at():
    r = sql(insert_dispatched(A) + "; UPDATE staged_provider_responses SET response='{\"v\": 1}' WHERE session_id='sess-1'", success=False)
    assert r.returncode != 0 and 'completion requires completed_at' in r.stderr, r.stderr
