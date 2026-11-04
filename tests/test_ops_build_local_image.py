"""The former native build is now an alias to the one amd64 owner workflow.

Build safety tests moved to test_exact_image_build.py, which executes real git
archives and fake Docker, rather than asserting a retired shell implementation.
"""
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_local_build_alias_preserves_goal_and_paths_without_native_arch_fork(tmp_path):
    docker=tmp_path/'scripts/docker';ops=tmp_path/'scripts/ops'
    docker.mkdir(parents=True);ops.mkdir(parents=True)
    script=docker/'build-local-image.sh';script.write_bytes((ROOT/'scripts/docker/build-local-image.sh').read_bytes())
    output=tmp_path/'args'
    (ops/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    result=subprocess.run(['bash',str(script),'--run-dir','/tmp/space path'],env=dict(os.environ,OUTPUT=str(output)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text().splitlines()==['--build','--run-dir','/tmp/space path']
    assert 'uname' not in script.read_text() and 'PREVIEW_ARCH' not in script.read_text()
