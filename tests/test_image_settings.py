import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('image_settings',Path(__file__).resolve().parents[1]/'scripts/docker/image_settings.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='DELIVERY_DATABASE_URL=postgresql://fixture/db\nDELIVERY_SUPABASE_URL=https://fixture.supabase.co\nDELIVERY_SUPABASE_ANON_KEY=public\nINTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\n'


def test_values_remain_literal_no_shell_eval_or_key_copy(tmp_path):
    path=tmp_path/'private.env';raw=BASE+'DEEPSEEK_API_KEY=fictional-$(touch never)\n';path.write_text(raw);path.chmod(0o600)
    assert m.validate(path)['DEEPSEEK_API_KEY']=='fictional-$(touch never)'
    assert path.read_text()==raw and not (tmp_path/'never').exists()


@pytest.mark.parametrize('body',[BASE.replace('postgresql://fixture/db','sqlite:///x'),BASE.replace('https://fixture.supabase.co','http://localhost'),
                                BASE.replace('DELIVERY_SUPABASE_ANON_KEY=public\n',''),BASE.replace('fictional-private-token-123456','short'),BASE+'DEEPSEEK_API_KEY=a\nDEEPSEEK_API_KEY=b\n'])
def test_invalid_settings_fail_without_private_value_echo(tmp_path,body):
    path=tmp_path/'private.env';path.write_text(body);path.chmod(0o600)
    with pytest.raises(ValueError) as error:m.validate(path)
    assert 'fictional-private-token' not in str(error.value) and 'postgresql://fixture/db' not in str(error.value)


def test_insecure_mode_or_missing_file_refused(tmp_path):
    path=tmp_path/'private.env'
    with pytest.raises(ValueError):m.validate(path)
    path.write_text(BASE);path.chmod(0o644)
    with pytest.raises(ValueError,match='mode 600'):m.validate(path)
