import importlib.util,os,pty,select,subprocess,sys,time
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1];SCRIPT=ROOT/'scripts/ops/settings.py'
SPEC=importlib.util.spec_from_file_location('terminal_settings',SCRIPT);m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

def test_hidden_input_and_confirmation_work_on_nonseekable_pty():
    master,slave=pty.openpty();terminal=os.ttyname(slave)
    code="import importlib.util; s=importlib.util.spec_from_file_location('s',%r); m=importlib.util.module_from_spec(s);s.loader.exec_module(m); assert m.hidden_prompt('KEY')=='secret-unique'; assert m.terminal_confirm('SAVE?')=='yes'; print('OK')"%str(SCRIPT)
    proc=subprocess.Popen([sys.executable,'-c',code],env={**os.environ,'PREVIEW_TTY':terminal},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output=b''
    try:
        deadline=time.time()+8;sent=False;confirmed=False
        while time.time()<deadline and proc.poll() is None:
            if select.select([master],[],[],0.1)[0]:output+=os.read(master,4096)
            if b'hidden' in output and not sent:os.write(master,b'secret-unique\n');sent=True
            if b'SAVE?' in output and not confirmed:os.write(master,b'yes\n');confirmed=True
        stdout,stderr=proc.communicate(timeout=2)
        assert proc.returncode==0,(output,stdout,stderr)
        assert b'secret-unique' not in output+stdout+stderr
    finally:
        if proc.poll() is None:proc.kill()
        os.close(master);os.close(slave)


def test_per_key_repo_fallback_accepts_dotenv_syntax_and_reports_empty(tmp_path):
    preview=tmp_path/'preview.env';preview.write_text('DELIVERY_DATABASE_URL=postgresql://fixture\n');preview.chmod(0o600)
    repo=tmp_path/'repo';repo.mkdir();dot=repo/'.env';dot.write_text("export REQUI_ADMIN_DATABASE_URL = 'postgresql://admin'\n");dot.chmod(0o600)
    env={'PREVIEW_SETTINGS_FILE':str(preview)}
    assert m.load(repo,env)['REQUI_ADMIN_DATABASE_URL']=='postgresql://admin'
    assert str(dot) in m.source_report(repo,'REQUI_ADMIN_DATABASE_URL',env)
    dot.write_text('REQUI_ADMIN_DATABASE_URL=\n')
    report=m.source_report(repo,'REQUI_ADMIN_DATABASE_URL',env)
    assert 'empty' in report and str(dot) in report and str(preview) in report
