import io,json,runpy,sys,zipfile
from pathlib import Path
from types import SimpleNamespace


def test_failure_zip_is_bounded_scrubbed_and_excludes_env(monkeypatch,capsys):
    import subprocess
    monkeypatch.setattr(sys,'argv',['preview-debug.py','--code','PIN','--step','grounding'])
    def run(command,**kwargs):
        assert command[:2] in (['git','rev-parse'],['docker','logs'])
        return SimpleNamespace(stdout='fakehead\n' if command[0]=='git' else 'postgresql://private:secret@db/x\nBearer secret123456\nAPI_KEY=another-secret\n')
    monkeypatch.setattr(subprocess,'run',run)
    runpy.run_path('scripts/ops/preview-debug.py',run_name='__main__')
    path=Path(capsys.readouterr().out.split('Debug ZIP: ')[1].splitlines()[0])
    try:
        with zipfile.ZipFile(path) as z:
            assert set(z.namelist())=={'facts.json','container.log'}
            assert json.loads(z.read('facts.json'))['failure_code']=='PIN'
            text=z.read('container.log').decode()
            assert 'private:secret' not in text and 'secret123456' not in text and 'another-secret' not in text
        assert path.stat().st_mode&0o777==0o600
    finally:path.unlink()
