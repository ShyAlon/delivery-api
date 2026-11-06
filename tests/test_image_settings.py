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


def test_missing_token_generated_saved_and_reused(tmp_path,capsys):
    path=tmp_path/'private.env';path.write_text(BASE.replace('INTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\n',''));path.chmod(0o600)
    values=m.validate(path,generate_missing_token=True)
    token=values['INTERVIEW_ENGINE_TOKEN']
    assert len(token)>=24 and token not in path.read_text().replace('INTERVIEW_ENGINE_TOKEN='+token,'')
    saved=path.read_text();assert saved.count('INTERVIEW_ENGINE_TOKEN=')==1
    assert path.stat().st_mode&0o077==0
    out=capsys.readouterr();assert token not in out.out and token not in out.err
    assert m.validate(path)['INTERVIEW_ENGINE_TOKEN']==token
    before=path.read_bytes();m.validate(path,generate_missing_token=True);assert path.read_bytes()==before


def test_missing_token_without_apply_names_generation_fix(tmp_path):
    path=tmp_path/'private.env';path.write_text(BASE.replace('INTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\n',''));path.chmod(0o600)
    with pytest.raises(ValueError) as error:m.validate(path)
    text=str(error.value);assert 'preview --apply' in text and 'generated' in text


def test_quoted_placeholder_and_short_token_name_the_removal_fix(tmp_path):
    for raw in ('INTERVIEW_ENGINE_TOKEN="local-interview-engine-token"\n',"INTERVIEW_ENGINE_TOKEN='short'\n",'INTERVIEW_ENGINE_TOKEN=\n'):
        path=tmp_path/'private.env';path.write_text(BASE.replace('INTERVIEW_ENGINE_TOKEN=fictional-private-token-123456\n',raw));path.chmod(0o600)
        with pytest.raises(ValueError) as error:m.validate(path,generate_missing_token=True)
        text=str(error.value)
        assert 'delete the INTERVIEW_ENGINE_TOKEN line' in text and 'generated' in text
        assert 'local-interview-engine-token' not in text and path.read_text().startswith('DELIVERY_DATABASE_URL')


def test_container_env_receives_exact_parsed_values_unquoted(tmp_path):
    # The real owner shape: every value quoted by the settings writer, plus a generated token.
    body=''.join(k+'="'+v+'"\n' for k,v in (
        ('DELIVERY_DATABASE_URL','postgresql://postgres.fixture:6543/postgres?sslmode=require'),
        ('DELIVERY_SUPABASE_URL','https://fixture.supabase.co'),
        ('DELIVERY_SUPABASE_ANON_KEY','fictional-public')))
    path=tmp_path/'private.env';path.write_text(body);path.chmod(0o600)
    values=m.validate(path,generate_missing_token=True)
    out=tmp_path/'container.env'
    m.write_container_env(out,values)
    text=out.read_text()
    assert 'DELIVERY_DATABASE_URL=postgresql://postgres.fixture:6543/postgres?sslmode=require\n' in text
    assert 'INTERVIEW_ENGINE_TOKEN='+values['INTERVIEW_ENGINE_TOKEN']+'\n' in text
    assert '"' not in text and "'" not in text and out.stat().st_mode&0o077==0


OLLAMA_FIX='http://host.docker.internal:11434/v1'


def test_ollama_route_without_base_url_names_container_loopback_fix(tmp_path):
    path=tmp_path/'private.env';path.write_text(BASE+'REQUI_PIN_PROVIDERS=ollama\n');path.chmod(0o600)
    with pytest.raises(ValueError) as error:m.validate(path)
    text=str(error.value)
    assert 'localhost is the container itself' in text and OLLAMA_FIX in text


def test_ollama_route_with_container_loopback_url_refused_with_named_fix(tmp_path):
    for host in ('localhost','127.0.0.1','[::1]'):
        path=tmp_path/'private.env'
        path.write_text(BASE+'REQUI_PIN_PROVIDERS=ollama\nOLLAMA_BASE_URL="http://'+host+':11434/v1"\n');path.chmod(0o600)
        with pytest.raises(ValueError) as error:m.validate(path)
        text=str(error.value)
        assert 'localhost is the container itself' in text and OLLAMA_FIX in text


def test_ollama_route_with_host_gateway_url_passes(tmp_path):
    path=tmp_path/'private.env'
    path.write_text(BASE+'REQUI_PIN_PROVIDERS=ollama\nOLLAMA_BASE_URL="'+OLLAMA_FIX+'"\n');path.chmod(0o600)
    assert m.validate(path)['OLLAMA_BASE_URL']==OLLAMA_FIX


def test_non_ollama_route_needs_no_ollama_url(tmp_path):
    path=tmp_path/'private.env';path.write_text(BASE+'REQUI_PIN_PROVIDERS=deepseek\n');path.chmod(0o600)
    assert 'OLLAMA_BASE_URL' not in m.validate(path)


def test_ollama_route_missing_base_url_derived_saved_and_reused(tmp_path):
    path=tmp_path/'private.env';path.write_text(BASE+'REQUI_PIN_PROVIDERS=ollama\n');path.chmod(0o600)
    values=m.validate(path,generate_missing_token=True)
    assert values['OLLAMA_BASE_URL']==m.OLLAMA_DERIVED_BASE_URL
    saved=path.read_text();assert saved.count('OLLAMA_BASE_URL=')==1 and 'OLLAMA_BASE_URL='+m.OLLAMA_DERIVED_BASE_URL+'\n' in saved
    assert path.stat().st_mode&0o077==0
    before=path.read_bytes()
    assert m.validate(path)['OLLAMA_BASE_URL']==m.OLLAMA_DERIVED_BASE_URL
    assert path.read_bytes()==before


def test_ollama_route_missing_base_url_review_names_apply_derivation(tmp_path):
    path=tmp_path/'private.env';body=BASE+'REQUI_PIN_PROVIDERS=ollama\n';path.write_text(body);path.chmod(0o600)
    with pytest.raises(ValueError) as error:m.validate(path)
    text=str(error.value)
    assert 'preview --apply' in text and OLLAMA_FIX in text and path.read_text()==body


def test_ollama_route_explicit_loopback_never_overridden(tmp_path):
    path=tmp_path/'private.env';body=BASE+'REQUI_PIN_PROVIDERS=ollama\nOLLAMA_BASE_URL="http://localhost:11434/v1"\n'
    path.write_text(body);path.chmod(0o600)
    with pytest.raises(ValueError) as error:m.validate(path,generate_missing_token=True)
    assert OLLAMA_FIX in str(error.value) and path.read_text()==body


def test_deepseek_route_derives_no_ollama_url(tmp_path):
    path=tmp_path/'private.env';path.write_text(BASE+'REQUI_PIN_PROVIDERS=deepseek\n');path.chmod(0o600)
    values=m.validate(path,generate_missing_token=True)
    assert 'OLLAMA_BASE_URL' not in values and 'OLLAMA_BASE_URL' not in path.read_text()
