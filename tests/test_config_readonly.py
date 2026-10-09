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
    for key in ('DELIVERY_DATABASE_URL','DELIVERY_DB_PATH','TURSO_DATABASE_URL','TURSO_AUTH_TOKEN'):
        env.pop(key,None)
    if selected:env['DELIVERY_DATABASE_URL']=selected
    code="""from pathlib import Path
original=Path.mkdir
def deny(self,*args,**kwargs):
    raise OSError(30,'Read-only file system',str(self))
Path.mkdir=deny
import delivery_api.config
"""
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert not home.exists()


def test_store_open_creates_only_selected_parent(tmp_path):
    from delivery_api.store import SQLiteStore
    path=tmp_path/'selected'/'nested'/'delivery.db'
    assert not path.parent.exists()
    store=SQLiteStore(str(path))
    assert path.is_file()
    store._db.close()
