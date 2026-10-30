from pathlib import Path
import subprocess,os
ROOT=Path(__file__).parents[1]
SCRIPT=ROOT/'scripts/ops/preview-exec.sh'

def test_step9_failure_preserves_container_but_removes_temporary_settings(tmp_path):
    source=SCRIPT.read_text();cleanup=source[source.index('cleanup(){'):source.index('\nSTEPS_TOTAL=')]
    fake=tmp_path/'docker';log=tmp_path/'log';fake.write_text('#!/bin/sh\necho "$*" >> "$LOG"\n');fake.chmod(0o755)
    work=tmp_path/'private';work.mkdir();(work/'secret.env').write_text('secret')
    result=subprocess.run(['bash','-c',cleanup+'\nSTARTED=1; KEEP_FOR_INSPECTION=1; NAME=requi-preview-exec; WORK="$1"; cleanup','_',str(work)],env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'LOG':str(log)},capture_output=True,text=True)
    assert result.returncode==0
    assert not work.exists()
    assert not log.exists(),'step9 failure must not remove the running container'
    assert 'docker logs' in result.stdout and 'docker rm -f' in result.stdout

def test_failure_repeats_named_browser_evidence_inside_abort():
    source=SCRIPT.read_text()
    assert 'BROWSER_OUT=$(' in source
    assert 'browser errors repeated for inspection' in source
    assert 'KEEP_FOR_INSPECTION=1' in source
