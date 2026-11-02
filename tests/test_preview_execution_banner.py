"""Exercise the production summary with fake container logs, no live service."""
import os,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
@pytest.mark.parametrize('logs,state',[('Cloud execution: OPEN (admitted)','OPEN'),('Cloud execution: CLOSED - money gateway unavailable','CLOSED'),('booting','UNVERIFIED'),('Cloud execution: OPEN\nCloud execution: CLOSED - failed','CLOSED'),('Cloud execution: CLOSED - failed\nCloud execution: OPEN','OPEN')])
def test_banner_uses_last_explicit_application_status(logs,state):
    script=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    begin=script.index('EXECUTION_STATUS=$(docker logs')
    end=script.index('echo "Running build:',begin)
    body='source "$1"; docker(){ printf "%s\\n" "$FAKE_LOGS"; }; scrub(){ cat; }; NAME=fake; PORT=8001; PROVIDER=both; '+script[begin:end]
    run=subprocess.run(['bash','-c',body,'bash',str(ROOT/'scripts/ops/terminal-log.sh')],env={**os.environ,'FAKE_LOGS':logs},capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert run.stdout.count('READY:')==1
    assert 'execution '+state in run.stdout
    assert ('WARNING:' in run.stdout)==(state!='OPEN')
    assert 'Both routes admitted' not in run.stdout
    if state!='OPEN':assert 'execution OPEN' not in run.stdout
