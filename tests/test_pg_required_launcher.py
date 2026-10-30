"""PG-only launch and import must fail closed before starting a service."""
from pathlib import Path
import os,subprocess,sys
import pytest

ROOT=Path(__file__).parents[1]

@pytest.mark.parametrize('path',['scripts/run-ui.sh','scripts/build-dist.sh'])
def test_launcher_has_no_implicit_local_storage_default(path):
    script=(ROOT/path).read_text()
    if path.endswith('build-dist.sh'):script=script.split("cat > \"$STAGE/run-require.sh\" <<'EOF'",1)[1].split('\nEOF',1)[0]
    assert 'sqlite' not in script.lower()
    assert 'delivery_api.launch_profile' in script
    assert 'Data: $DELIVERY_DATABASE_URL' not in script

@pytest.mark.parametrize('selected',['','sqlite:////tmp/selected.db','/tmp/selected.db','mysql://fake/config-only'])
def test_config_rejects_missing_or_non_pg_without_leaking_dsn(tmp_path,selected):
    env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'DELIVERY_DATABASE_URL':selected,
        'DELIVERY_SUPABASE_URL':'https://test.invalid','DELIVERY_SUPABASE_ANON_KEY':'fake',
        'HOME':str(tmp_path/'home'),'XDG_DATA_HOME':str(tmp_path/'data')}
    got=subprocess.run([sys.executable,'-m','delivery_api.launch_profile'],env=env,capture_output=True,text=True)
    assert got.returncode!=0
    assert 'PostgreSQL' in got.stderr
    assert selected not in got.stderr if selected else True
    assert not (tmp_path/'data').exists()


def test_profile_validates_without_connecting_or_printing_credentials(pg_runtime_environment):
    env=pg_runtime_environment()
    got=subprocess.run([sys.executable,'-m','delivery_api.launch_profile'],env=env,capture_output=True,text=True)
    assert got.returncode==0,got.stderr
    assert 'PostgreSQL' in got.stdout
    assert env['DELIVERY_DATABASE_URL'] not in got.stdout+got.stderr


def test_engine_launch_uses_same_pg_and_tenant_mode_without_file_fallback(monkeypatch,pg_runtime_environment):
    from delivery_api.launch_profile import engine_environment
    env=pg_runtime_environment()
    env.update(INTERVIEW_ENGINE_TOKEN='private-fixture-token-1234567890',INTERVIEW_ENGINE_SESSIONS_FILE='1')
    got=engine_environment(env)
    assert got['INTERVIEW_ENGINE_SESSIONS_DSN']==env['DELIVERY_DATABASE_URL']
    assert got['INTERVIEW_ENGINE_TENANTS']=='1'
    assert got['INTERVIEW_ENGINE_SESSIONS_FILE']=='0'
    assert got['INTERVIEW_ENGINE_TOKEN']==env['INTERVIEW_ENGINE_TOKEN']
    with pytest.raises(ValueError,match='private'):
        engine_environment({**env,'INTERVIEW_ENGINE_TOKEN':'local-interview-engine-token'})


def test_engine_wrapper_propagates_exact_profile_to_child_without_connecting(pg_runtime_environment,tmp_path):
    import json
    env=pg_runtime_environment()
    env['INTERVIEW_ENGINE_TOKEN']='private-fixture-token-1234567890'
    env['INTERVIEW_ENGINE_SESSIONS_FILE']='1'
    code="import os,json; print(json.dumps({'same':os.environ['INTERVIEW_ENGINE_SESSIONS_DSN']==os.environ['DELIVERY_DATABASE_URL'],'tenant':os.environ['INTERVIEW_ENGINE_TENANTS'],'file':os.environ['INTERVIEW_ENGINE_SESSIONS_FILE']}))"
    got=subprocess.run([sys.executable,'-m','delivery_api.launch_profile','--engine',sys.executable,'-c',code],env=env,capture_output=True,text=True)
    assert got.returncode==0,got.stderr
    assert json.loads(got.stdout)=={'same':True,'tenant':'1','file':'0'}
    assert env['DELIVERY_DATABASE_URL'] not in got.stdout+got.stderr


def test_source_launcher_does_not_override_dotenv_token_with_local_default():
    script=(ROOT/'scripts/run-ui.sh').read_text()
    assert 'local-interview-engine-token' not in script
    assert '--validate-engine' in script and '--engine' in script


def test_pg_driver_and_pool_are_required_runtime_dependencies():
    import tomllib
    project=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']
    dependencies=project['dependencies']
    assert any(value.startswith('psycopg[binary]') for value in dependencies)
    assert any(value.startswith('psycopg-pool') for value in dependencies)
    build=(ROOT/'scripts/build-dist.sh').read_text()
    assert 'PG_DEPS=' in build and 'psycopg[binary]' in build and 'psycopg-pool' in build
