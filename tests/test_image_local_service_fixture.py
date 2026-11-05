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
    assert "socket_dir=shared/'socket'" in text
    assert "Path('/tmp/fixture-keys')" in text
    assert "Path('/tmp/fixture-state.json')" in text
    assert "(shared/'ca.pem')" in text
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


def test_real_owned_fixture_initializes_migrated_pg_and_zero_cost_pin(tmp_path):
    """Native process validation only. This is not the unchanged Docker gate."""
    import json,time,signal
    import pgserver
    import psycopg
    binary=Path(pgserver.__file__).parent/'pginstall/bin'
    shared=tmp_path/'shared';shared.mkdir()
    state=tmp_path/'state.json'
    env={k:v for k,v in os.environ.items() if k not in ('DELIVERY_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL','DEEPSEEK_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY')}
    env['REQUI_SMOKE_PG_BIN']=str(binary)
    body="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]+'/scripts/docker');from local_service_fixture import main;main(root=Path(sys.argv[1]),shared=Path(sys.argv[2]),private=Path(sys.argv[3]),state=Path(sys.argv[4]),auth_port=0,provider_port=0)"
    with (tmp_path/'private.log').open('w') as log:
        child=subprocess.Popen([sys.executable,'-c',body,str(ROOT),str(shared),str(tmp_path/'keys'),str(state)],env=env,stdout=log,stderr=log)
        try:
            for _ in range(200):
                if state.exists():break
                if child.poll() is not None:raise AssertionError('owned native fixture startup failed: '+(tmp_path/'private.log').read_text())
                time.sleep(.05)
            assert state.exists(),'owned fixture readiness timed out'
            handoff=json.loads(state.read_text())
            assert handoff['dsn'].startswith('postgresql:///test_tenant_')
            assert state.stat().st_mode&0o077==0
            from psycopg.conninfo import conninfo_to_dict
            fields=conninfo_to_dict(handoff['dsn']);fields['user']='sandbox'
            with psycopg.connect(**fields) as conn:
                assert conn.execute('SELECT balance_units FROM credit_accounts').fetchone()==(0,)
                pin=conn.execute('SELECT payload FROM model_pin_revisions').fetchone()[0]
                assert pin['row']['expense_input']==0 and pin['row']['retail_output']==0
                assert conn.execute("SELECT count(*) FROM auth.sessions").fetchone()[0]==2
            assert sorted(p.name for p in shared.iterdir())==['ca.pem','socket']
            # Exercise the real container entry's read-only startup checks too,
            # not merely the fixture's successful database initialization.
            service_env=env|{'DELIVERY_DATABASE_URL':handoff['dsn'],
                'DELIVERY_SUPABASE_URL':'https://image-fixture.supabase.co',
                'DELIVERY_SUPABASE_ANON_KEY':'fictional-public-key',
                'INTERVIEW_ENGINE_TOKEN':'fixture-private-engine-token-123456789',
                'VMODEL_ARTIFACTS_DIR':str(tmp_path/'artifacts'),
                'XDG_DATA_HOME':str(tmp_path/'data')}
            probe=subprocess.run([sys.executable,str(ROOT/'scripts/docker/preflight.py')],env=service_env,capture_output=True,text=True,timeout=30)
            assert probe.returncode==0,probe.stdout+probe.stderr
            import socket,urllib.request
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            with (tmp_path/'entry.log').open('w') as output:
                entry=subprocess.Popen([sys.executable,str(ROOT/'scripts/docker/full_entry.py')],
                    env=service_env|{'DELIVERY_HOST':'127.0.0.1','DELIVERY_PORT':str(port),
                    'SSL_CERT_FILE':str(shared/'ca.pem'),'REQUESTS_CA_BUNDLE':str(shared/'ca.pem')},stdout=output,stderr=output)
                try:
                    for _ in range(100):
                        if entry.poll() is not None:raise AssertionError((tmp_path/'entry.log').read_text())
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                                if json.load(response).get('ok') is True:break
                        except OSError:time.sleep(.05)
                    else:raise AssertionError((tmp_path/'entry.log').read_text())
                finally:entry.terminate();entry.wait(timeout=10)


        finally:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=10)
