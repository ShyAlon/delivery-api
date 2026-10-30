"""PG-only entry points must not inherit retired settings from shell/dotenv."""
import os,subprocess,sys
from pathlib import Path
import pytest

SRC=Path(__file__).parents[1]/'src'

@pytest.mark.parametrize('origin',['shell','dotenv'])
def test_pg_config_ignores_retired_variables_without_printing_values(tmp_path,origin):
    env={k:v for k,v in os.environ.items() if not k.startswith('TURSO_')}
    env.update(PYTHONPATH=str(SRC),DELIVERY_DATABASE_URL='postgresql://fixture.invalid/unused',
        DELIVERY_SUPABASE_URL='https://fixture.supabase.co',DELIVERY_SUPABASE_ANON_KEY='public-fixture')
    retired={'TURSO_DATABASE_URL':'SECRET_OLD_DATABASE','TURSO_AUTH_TOKEN':'SECRET_OLD_TOKEN','TURSO_SYNC_INTERVAL':'99'}
    if origin=='shell':env.update(retired)
    else:(tmp_path/'.env').write_text('\n'.join(k+'='+v for k,v in retired.items()))
    code="from dotenv import load_dotenv;load_dotenv('.env');from delivery_api import config;import os;assert config.CLOUD_TENANTS;assert not any(os.environ.get(k) for k in os.environ if k.startswith('TURSO_'));print('PG CONFIG OK')"
    out=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert out.returncode==0,out.stderr
    text=out.stdout+out.stderr
    assert 'PG CONFIG OK' in text
    for name in retired:assert name in text
    for value in retired.values():assert value not in text


def test_helper_clears_unknown_retired_names_is_idempotent_and_keeps_pg_auth():
    from delivery_api.runtime_environment import neutralize_retired_storage
    env={'TURSO_OLD_EXTRA':'secret-extra','TURSO_AUTH_TOKEN':'secret-token','DELIVERY_DATABASE_URL':'pg-target','DELIVERY_SUPABASE_ANON_KEY':'public-key'}
    lines=[]
    assert neutralize_retired_storage(env,report=lines.append)==('TURSO_AUTH_TOKEN','TURSO_OLD_EXTRA')
    assert env['DELIVERY_DATABASE_URL']=='pg-target' and env['DELIVERY_SUPABASE_ANON_KEY']=='public-key'
    assert not any(v for k,v in env.items() if k.startswith('TURSO_'))
    assert 'secret' not in '\n'.join(lines)
    assert neutralize_retired_storage(env,report=lines.append)==()
    assert len(lines)==2
