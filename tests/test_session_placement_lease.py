"""PR A schema contract: placement lease columns and the session journal.

Runs against the isolated migrated Postgres (pg_harness). Verifies the
migration's shape and that the journal carries the same tenant isolation and
immutability posture as its parent registry - never a real database.
"""
import os
import subprocess
import pytest

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
if DSN and 'dbname=test_tenant_' not in DSN:
    raise RuntimeError('refusing integration tests outside an isolated test_tenant_ database')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'


def sql(query, owner=A, success=True):
    prefix = 'BEGIN; SET LOCAL ROLE authenticated; '
    if owner is not None:
        prefix += f"SELECT set_config('request.jwt.claim.sub','{owner}',true); SELECT set_config('request.jwt.claim.session_id','{owner}',true); "
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq', '-f', '-'], input=prefix + query + '; ROLLBACK;', text=True,
                            capture_output=True)
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return result.stdout.strip().splitlines()


def run(query, success=True):
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq', '-c', query], text=True, capture_output=True)
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return result.stdout.strip().splitlines()


@pytest.fixture(scope='module', autouse=True)
def seed():
    if not DSN: return
    run(f"INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES ('la','Lease','pending',0,0,'{A}') ON CONFLICT DO NOTHING")
    run(f"INSERT INTO engine_sessions(session_id,owner_id,task_id,generation,created_at) VALUES ('lease-sa','{A}','la',1,0) ON CONFLICT DO NOTHING")


def test_lease_columns_exist_with_safe_defaults():
    rows = run("SELECT placement_instance, lease_epoch, lease_expires_at, published_checkpoint_ns FROM engine_sessions WHERE session_id='lease-sa'")
    assert rows[-1] == '|0|0|'


def test_journal_tenant_isolation():
    run("INSERT INTO engine_session_journal VALUES ('lease-sa',1,'{}'::jsonb,0) ON CONFLICT DO NOTHING")
    assert sql("SELECT cursor FROM engine_session_journal WHERE session_id='lease-sa'", A)[-1] == '1'
    # Owner B sees nothing of A's journal and cannot write into A's session.
    assert sql("SELECT count(*) FROM engine_session_journal WHERE session_id='lease-sa'", B)[-1] == '0'
    sql("INSERT INTO engine_session_journal VALUES ('lease-sa',2,'{}'::jsonb,0)", B, success=False)


def test_journal_pages_are_immutable_for_authenticated():
    run("INSERT INTO engine_session_journal VALUES ('lease-sa',3,'{\"a\":1}'::jsonb,0) ON CONFLICT DO NOTHING")
    sql("UPDATE engine_session_journal SET page='{}'::jsonb WHERE session_id='lease-sa' AND cursor=3", success=False)
    sql("DELETE FROM engine_session_journal WHERE session_id='lease-sa' AND cursor=3", success=False)


def test_journal_requires_a_real_session():
    sql("INSERT INTO engine_session_journal VALUES ('no-such-session',1,'{}'::jsonb,0)", success=False)
