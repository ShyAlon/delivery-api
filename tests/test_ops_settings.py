"""File-first credentials without shell execution, leaks or silent project mixing."""
import importlib.util
from pathlib import Path
import os
import pytest
SPEC=importlib.util.spec_from_file_location('ops_settings',Path(__file__).parents[1]/'scripts/ops/settings.py')
m=importlib.util.module_from_spec(SPEC)

def module():
    SPEC.loader.exec_module(m)
    return m

def save(path,body):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body);path.chmod(0o600)

def test_file_first_per_key_fallback_and_no_interpolation(tmp_path):
    s=module();root=tmp_path/'repo';preview=tmp_path/'preview.env'
    save(root/'.env',"DEEPSEEK_API_KEY='file-${NO_EXPAND}-$(touch /bad)'\nDELIVERY_SUPABASE_URL=https://same\n")
    save(preview,'DELIVERY_DATABASE_URL=postgresql://saved\nDELIVERY_SUPABASE_URL=https://same\n')
    result=s.load(root,{'PREVIEW_SETTINGS_FILE':str(preview),'DEEPSEEK_API_KEY':'shell'})
    assert result['DEEPSEEK_API_KEY']=='file-${NO_EXPAND}-$(touch /bad)'
    assert result['DELIVERY_DATABASE_URL']=='postgresql://saved'

def test_conflicting_project_pair_refuses_without_values(tmp_path):
    s=module();save(tmp_path/'.env','DELIVERY_SUPABASE_URL=https://second-secret\nDEEPSEEK_API_KEY=second\n')
    p=tmp_path/'preview.env';save(p,'DELIVERY_SUPABASE_URL=https://first-secret\n')
    with pytest.raises(s.SettingsError) as exc:s.load(tmp_path,{'PREVIEW_SETTINGS_FILE':str(p)})
    assert 'conflict' in str(exc.value) and 'secret' not in str(exc.value)

@pytest.mark.parametrize('body',['DEEPSEEK_API_KEY=a\nDEEPSEEK_API_KEY=b\n',"DEEPSEEK_API_KEY='unterminated\n"])
def test_bad_files_are_value_free_errors(tmp_path,body):
    s=module();save(tmp_path/'.env',body)
    with pytest.raises(s.SettingsError) as exc:s.load(tmp_path,{'XDG_CONFIG_HOME':str(tmp_path/'none')})
    assert 'unterminated' not in str(exc.value)

def test_world_readable_rejected(tmp_path):
    s=module();save(tmp_path/'.env','DEEPSEEK_API_KEY=private\n');(tmp_path/'.env').chmod(0o644)
    with pytest.raises(s.SettingsError,match='600'):s.load(tmp_path,{'XDG_CONFIG_HOME':str(tmp_path/'none')})

def test_missing_only_hidden_prompt_and_declined_save(tmp_path):
    s=module();calls=[];values={'DEEPSEEK_API_KEY':'existing'}
    s.require(values,['DEEPSEEK_API_KEY','RESET_OWNER_ID'],prompt=lambda name:calls.append(name) or 'owner')
    assert calls==['RESET_OWNER_ID'] and values['RESET_OWNER_ID']=='owner'
    p=tmp_path/'save.env';s.save_confirmed(p,values,confirm=lambda _: 'no')
    assert not p.exists()

def test_atomic_save_preserves_other_names_and_quotes(tmp_path):
    s=module();p=tmp_path/'save.env';save(p,"OTHER='keep'\nDEEPSEEK_API_KEY=old\n")
    s.save_confirmed(p,{'DEEPSEEK_API_KEY':"new'quoted$literal"},confirm=lambda _:'yes')
    assert p.stat().st_mode & 0o777==0o600
    assert s.read_file(p)['OTHER']=='keep'
    assert s.read_file(p)['DEEPSEEK_API_KEY']=="new'quoted$literal"

def test_only_allowlisted_names_are_exported(tmp_path):
    s=module();save(tmp_path/'.env','PATH=/evil\nBASH_ENV=/evil\nDEEPSEEK_API_KEY=good\n')
    result=s.load(tmp_path,{'XDG_CONFIG_HOME':str(tmp_path/'none')})
    assert result=={'DEEPSEEK_API_KEY':'good'}

def test_exec_delivers_only_to_child_environment_not_output_or_argv(tmp_path):
    import subprocess,sys
    root=tmp_path/'repo';save(root/'.env',"DEEPSEEK_API_KEY='secret-$(touch NEVER)-literal'\nBASH_ENV=NEVER\n")
    env={'PATH':os.environ['PATH'],'HOME':str(tmp_path),'XDG_CONFIG_HOME':str(tmp_path/'cfg')}
    command=[sys.executable,str(Path(__file__).parents[1]/'scripts/ops/settings.py'),'--repo',str(root),'exec','--',sys.executable,'-c',"import os,sys; assert os.environ['DEEPSEEK_API_KEY']=='secret-$(touch NEVER)-literal'; assert 'BASH_ENV' not in os.environ; print('CHILD-OK')"]
    result=subprocess.run(command,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert result.stdout=='CHILD-OK\n' and 'secret-' not in result.stderr
    assert not (root/'NEVER').exists()

def test_shell_fallback_identity_is_complete_or_not_mixed(tmp_path):
    s=module();env={'XDG_CONFIG_HOME':str(tmp_path/'cfg'),'DELIVERY_DATABASE_URL':'postgresql://shell','DELIVERY_SUPABASE_URL':'https://shell','DELIVERY_SUPABASE_ANON_KEY':'key'}
    assert s.load(tmp_path,env)['DELIVERY_SUPABASE_ANON_KEY']=='key'
    save(tmp_path/'.env','DELIVERY_DATABASE_URL=postgresql://file\n')
    result=s.load(tmp_path,env)
    assert 'DELIVERY_SUPABASE_URL' not in result and 'DELIVERY_SUPABASE_ANON_KEY' not in result

def test_missing_secret_without_tty_never_uses_echoing_fallback(tmp_path,monkeypatch):
    s=module();monkeypatch.setenv('PREVIEW_TTY',str(tmp_path/'absent'))
    with pytest.raises(s.SettingsError,match='requires a terminal'):s.require({},['DEEPSEEK_API_KEY'])

def test_delivery_container_file_excludes_providers_and_forced_flags(tmp_path,monkeypatch):
    s=module();monkeypatch.setenv('DEEPSEEK_API_KEY','secret');monkeypatch.setenv('DELIVERY_OPENAI_API_KEY','secret');monkeypatch.setenv('DELIVERY_SUPABASE_ANON_KEY','public');monkeypatch.setenv('DELIVERY_CLOUD_EXECUTION','1')
    path=tmp_path/'docker.env';s.delivery_env(path)
    assert 'secret' not in path.read_text() and 'DELIVERY_CLOUD_EXECUTION=' not in path.read_text()
    assert 'DELIVERY_SUPABASE_ANON_KEY=public' in path.read_text() and path.stat().st_mode & 0o077==0

def test_owner_wrappers_use_one_loader_and_keep_active_session_check():
    root=Path(__file__).parents[1]
    for name in ('stop-all','task-reset'):
        text=(root/'scripts/owner'/f'{name}.py').read_text()
        assert 'settings.load(root,explicit=args.env_file)' in text and 'parse_stream' not in text
        assert text.index('if not store.auth_session_active()')<text.index('settings.save_confirmed(')
        assert "values.get('RESET_AUTH_SESSION_ID')" in text
        assert "identity(args.owner or values.get('RESET_OWNER_ID')" in text


def test_save_does_not_persist_test_db_settings(tmp_path):
    s=module();path=tmp_path/'saved.env'
    s.save_confirmed(path,{'DEEPSEEK_API_KEY':'key','DELIVERY_TEST_POSTGRES_DSN':'never'},confirm=lambda _:'yes')
    assert 'DELIVERY_TEST_' not in path.read_text()


def test_explicit_operator_config_survives_file_first_container_boundary(tmp_path,monkeypatch):
    s=module();root=tmp_path/'repo';preview=tmp_path/'preview.env'
    save(preview,"REQUI_OPERATOR_SUBJECT=verified-subject\nREQUI_OPERATOR_REGISTRY_VERSION=reviewed-v1\nREQUI_OPERATOR_CAPABILITIES='[\"logs.read\"]'\nREQUI_OPERATOR_EVIL=not-allowed\n")
    values=s.load(root,{'PREVIEW_SETTINGS_FILE':str(preview)})
    assert set(values)==s.OPERATOR_SETTINGS
    for k,v in values.items():monkeypatch.setenv(k,v)
    monkeypatch.setenv('REQUI_OPERATOR_EVIL','no')
    target=tmp_path/'container.env';s.delivery_env(target)
    text=target.read_text()
    assert all(k+'='+values[k] in text for k in s.OPERATOR_SETTINGS)
    assert 'REQUI_OPERATOR_EVIL' not in text
