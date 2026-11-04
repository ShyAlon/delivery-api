from pathlib import Path
import ast
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_fixture_rejects_inherited_live_dsn_before_pg_or_writes():
    result=subprocess.run([sys.executable,str(ROOT/'scripts/docker/local_service_fixture.py')],
                          env=dict(os.environ,DELIVERY_DATABASE_URL='postgresql://never-contact.invalid/live'),capture_output=True,text=True)
    assert result.returncode!=0 and 'fixture refuses inherited database/provider settings' in result.stderr
    assert 'pg_harness' not in result.stderr


def test_fixture_container_is_separate_and_exports_no_private_keys():
    text=(ROOT/'scripts/docker/local_service_fixture.py').read_text();ast.parse(text)
    docker=(ROOT/'scripts/docker/Dockerfile.local-fixture').read_text()
    assert 'COPY src/delivery_api/ /' not in docker
    assert 'USER 10001:10001' in docker
    assert "socket_dir=Path('/fixture/socket')" in text
    assert "Path('/tmp/fixture-keys')" in text
    assert "Path('/tmp/fixture-state.json')" in text
    assert "'/fixture')/'ca.pem'" in text
    assert 'server_factory=Server' in text
    assert 'source overlay' not in docker


def test_retained_proof_is_fixed_read_only_sql_and_never_writes_success_rows():
    text=(ROOT/'scripts/docker/retained_image_proof.py').read_text();ast.parse(text)
    assert 'SET TRANSACTION READ ONLY' in text
    for source in ('worker_jobs','decision_reviews','provider_attempts','engine_session_journal','checkpoints'):
        assert source in text
    assert "len(jobs)!=2" in text and "row[0]!='settled'" in text
    assert 'INSERT ' not in text and 'UPDATE ' not in text and 'DELETE ' not in text
    assert "fields.get('host')!='/fixture/socket'" in text
