"""Rebuild is explicit local acceptance, never container deletion/tag pruning.

Real clean/dirty/snapshot/reuse/build safety lives in test_exact_image_build and
 test_image_workflow. Resource cleanup is invocation-owned in test_local_image_run.
"""
from pathlib import Path
import os
import subprocess
ROOT=Path(__file__).resolve().parents[1]


def test_rebuild_forwards_to_owned_acceptance_without_stopping_running_preview(tmp_path):
    ops=tmp_path/'ops';ops.mkdir();output=tmp_path/'args'
    script=ops/'preview-rebuild.sh';script.write_bytes((ROOT/'scripts/ops/preview-rebuild.sh').read_bytes())
    (ops/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    result=subprocess.run(['bash',str(script),'--run-dir','/tmp/space path'],env=dict(os.environ,OUTPUT=str(output)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text().splitlines()==['--accept-local','--run-dir','/tmp/space path']
    assert 'req image --accept-local' in result.stderr
    assert 'docker rm' not in script.read_text() and 'docker rmi' not in script.read_text()
    assert 'ssh ' not in script.read_text()
