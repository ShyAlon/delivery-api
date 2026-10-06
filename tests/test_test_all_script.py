import os,subprocess
from pathlib import Path

SCRIPT=Path(__file__).parents[1]/'scripts'/'test-all.sh'

def test_script_parses_and_rejects_unknown_repo():
    assert subprocess.run(['bash','-n',str(SCRIPT)]).returncode==0
    out=subprocess.run([str(SCRIPT),'--repo','nope'],capture_output=True,text=True)
    assert out.returncode==2 and 'unknown repo' in out.stderr

def test_script_never_touches_git_or_live_models():
    text=SCRIPT.read_text()
    assert 'git ' not in text.replace('Never touches git','')
    assert 'phi4' not in text.lower() and 'jev' not in text.lower()

def test_missing_checkout_is_a_finding_not_a_pass(tmp_path):
    env={**os.environ,'REQUI_RE_REPOS_ROOT':str(tmp_path)}
    out=subprocess.run([str(SCRIPT),'--repo','vmodel-contracts'],capture_output=True,text=True,env=env)
    assert out.returncode==1 and 'checkout not found' in out.stdout and 'NEED ATTENTION' in out.stdout


def test_no_log_path_is_printed_when_nothing_ran(tmp_path):
    env={**os.environ,'REQUI_RE_REPOS_ROOT':str(tmp_path)}
    out=subprocess.run([str(SCRIPT),'--repo','vmodel-contracts'],capture_output=True,text=True,env=env)
    assert 'Logs: none (no suite ran)' in out.stdout and '.test-all-logs' not in out.stdout
