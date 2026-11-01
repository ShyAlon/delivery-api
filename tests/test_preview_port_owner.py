"""Only listener evidence and explicit owner-reviewed stop advice, never a kill."""
import os
from pathlib import Path
import subprocess
import pytest
ROOT=Path(__file__).parents[1]

@pytest.mark.parametrize('rows,expected',[
    ('requi-preview-exec|abc123|127.0.0.1:8001->8000/tcp','docker stop requi-preview-exec'),
    ('other-service|def456|0.0.0.0:8001->80/tcp','NOT our preview container'),
    ('other|def456|0.0.0.0:9000->8001/tcp','No running Docker host-port mapping'),
    ('','No running Docker host-port mapping')])
def test_port_owner_evidence_only(tmp_path,rows,expected):
    bin=tmp_path/'bin';bin.mkdir();log=tmp_path/'calls'
    (bin/'docker').write_text('#!/bin/sh\necho "$*" >> "$CALL_LOG"\nprintf "%s\\n" "$DOCKER_ROWS"\n')
    (bin/'lsof').write_text('#!/bin/sh\nprintf "COMMAND PID USER NAME\\npython 4321 owner 127.0.0.1:8001 (LISTEN)\\n"\n')
    for p in bin.iterdir():p.chmod(0o755)
    env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'CALL_LOG':str(log),'DOCKER_ROWS':rows}
    result=subprocess.run(['bash','-c','source "$1"; port_busy_details 8001 requi-preview-exec','test',str(ROOT/'scripts/ops/lib.sh')],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert expected in result.stdout and 'python 4321' in result.stdout
    assert log.read_text().strip()=="ps --format {{.Names}}|{{.ID}}|{{.Ports}}"
    assert 'docker stop other' not in result.stdout
