"""Import config must not write to home, including a read-only container root."""
import os,subprocess,sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('selected',[None,'sqlite:////tmp/selected.db','/tmp/selected.db','postgresql://fake/config-only'])
def test_config_import_never_creates_default_home_directory(tmp_path,selected):
    home=tmp_path/'uncreated-home'
    env={**os.environ,'HOME':str(home),'XDG_DATA_HOME':str(home/'xdg'),
        'PYTHONPATH':str(Path(__file__).parents[1]/'src'),
        'DELIVERY_SUPABASE_URL':'https://test.invalid','DELIVERY_SUPABASE_ANON_KEY':'fake'}
    env['DELIVERY_DATABASE_URL']=selected or ''
    # Set, not removed: config calls load_dotenv(override=False), which fills any
    # variable that is missing from a .env file found above the repo, but never
    # replaces one that is set, even to an empty string (see tests/conftest.py).
    for key in ('TURSO_DATABASE_URL','TURSO_AUTH_TOKEN'):
        env[key]=''
    if selected:env['DELIVERY_DATABASE_URL']=selected
    code="""from pathlib import Path
original=Path.mkdir
def deny(self,*args,**kwargs):
    raise OSError(30,'Read-only file system',str(self))
Path.mkdir=deny
import delivery_api.config
"""
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
    if selected and selected.startswith('postgresql://'):
        assert result.returncode==0,result.stderr
    else:
        assert result.returncode!=0 and 'PostgreSQL' in result.stderr
    assert not home.exists()
