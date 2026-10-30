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
