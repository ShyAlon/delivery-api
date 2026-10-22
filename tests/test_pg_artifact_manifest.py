"""PR B schema contract: artifact manifest authority and tenant-owned byte storage.

Runs against the isolated migrated Postgres (pg_harness) as requi_backend with
tenant JWT claims - never a real database. The production migration is the
source of truth; no mirrored DDL here. Each scenario runs in ONE rolled-back
transaction because psql calls do not share state.
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
    prefix = 'BEGIN; SET LOCAL ROLE requi_backend; '
    if owner is not None:
        prefix += f"SELECT set_config('request.jwt.claim.sub','{owner}',true); SELECT set_config('request.jwt.claim.session_id','{owner}',true); "
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq', '-f', '-'], input=prefix + query + '; ROLLBACK;', text=True,
                            capture_output=True)
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return [l for l in result.stdout.strip().splitlines() if '-' not in l]  # drop set_config uuid echoes


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
    run(f"INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES ('ma','Manifest','pending',0,0,'{A}'),('mb','Manifest B','pending',0,0,'{B}') ON CONFLICT DO NOTHING")


def test_staged_rows_coexist_but_only_one_published_per_artifact_key():
    # Revision loops stage freely under the same key; the second PUBLISHED row for the key is rejected.
    sql("INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','srs.json','/a',0,'staged','h1',10),('ma',1,'spec','srs.json','/b',0,'staged','h2',11);"
        "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','srs.json','/a',0,'published','h1',10);"
        "INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','srs.json','/b',0,'published','h2',11)", success=False)
    # A different generation may publish its own pointer (revisions supported).
    sql("INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','srs.json','/a',0,'published','h1',10),"
        " ('ma',2,'spec','srs.json','/c',0,'published','h3',12)")


def test_publication_cas_demotes_and_promotes_atomically():
    out = sql("INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','items.json','/a',0,'published','h1',10),('ma',1,'spec','items.json','/b',0,'staged','h2',11);"
        "UPDATE task_artifacts SET publication_state='superseded' WHERE task_id='ma' AND generation=1 AND phase_key='spec' AND name='items.json' AND publication_state='published';"
        "UPDATE task_artifacts SET publication_state='published',published_at=1 WHERE task_id='ma' AND generation=1 AND phase_key='spec' AND name='items.json' AND publication_state='staged' AND sha256='h2';"
        "SELECT publication_state FROM task_artifacts WHERE task_id='ma' AND name='items.json' ORDER BY publication_state")
    assert out == ['published', 'superseded']
    # Replaying the promote CAS is a no-op: exactly-once publish, no double pointer.
    out = sql("INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at,publication_state,sha256,byte_size) VALUES"
        " ('ma',1,'spec','items.json','/b',0,'published','h2',11);"
        "UPDATE task_artifacts SET publication_state='published',published_at=2 WHERE task_id='ma' AND generation=1 AND phase_key='spec' AND name='items.json' AND publication_state='staged' AND sha256='h2' RETURNING publication_state")
    assert out == []


def test_artifact_bytes_tenant_isolated_no_oracle_no_leak():
    # Owner B cannot see A's row (no shared-row leak).
    assert sql("INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','deadbeef',decode('0102','hex'),2);"
               "SELECT count(*) FROM artifact_bytes" % A) == ['1']
    assert sql("SELECT count(*) FROM artifact_bytes", owner=B) == ['0']
    # Owner B stores the same content hash as a separate row: no collision, no existence oracle.
    assert sql("INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','deadbeef',decode('0102','hex'),2);"
               "SELECT count(*) FROM artifact_bytes" % B, owner=B) == ['1']


def test_artifact_bytes_immutable_and_conflicting_bytes_rejected():
    # Same hash with DIFFERENT bytes under one tenant conflicts loudly (PK), never silently dedups.
    sql("INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','cafe',decode('01','hex'),1);"
        "INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','cafe',decode('02','hex'),1)" % (A, A), success=False)
    # Immutable: no UPDATE or DELETE for the backend role.
    sql("INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','cafe',decode('01','hex'),1);"
        "UPDATE artifact_bytes SET bytes=decode('03','hex') WHERE owner_id='%s' AND sha256='cafe'" % (A, A), success=False)
    sql("INSERT INTO artifact_bytes(owner_id,sha256,bytes,byte_size) VALUES ('%s','cafe',decode('01','hex'),1);"
        "DELETE FROM artifact_bytes WHERE owner_id='%s' AND sha256='cafe'" % (A, A), success=False)
