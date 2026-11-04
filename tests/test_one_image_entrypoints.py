from pathlib import Path
import os
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('relative,goal',[('scripts/docker/build-local-image.sh','--build'),
                                         ('scripts/docker/build-full-image.sh','--build'),
                                         ('scripts/ops/preview-exec.sh','--preview'),
                                         ('scripts/ops/preview-rebuild.sh','--accept-local')])
def test_compatibility_entries_forward_to_one_workflow_without_docker_or_ssh(tmp_path,relative,goal):
    script=tmp_path/relative;script.parent.mkdir(parents=True,exist_ok=True)
    script.write_bytes((ROOT/relative).read_bytes())
    ops=tmp_path/'scripts/ops';ops.mkdir(parents=True,exist_ok=True)
    output=tmp_path/'args'
    (ops/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    result=subprocess.run(['bash',str(script),'--run-dir','/tmp/space path'],env=dict(os.environ,OUTPUT=str(output)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    args=output.read_text().splitlines()
    assert args[0]==goal and args[-2:]==['--run-dir','/tmp/space path']
    text=script.read_text()
    assert 'docker run' not in text and 'ssh ' not in text and 'docker build' not in text


def test_go_live_cannot_reintroduce_activation_mode():
    result=subprocess.run(['bash',str(ROOT/'scripts/ops/go-live.sh')],capture_output=True,text=True)
    assert result.returncode==2 and 'go-live is retired' in result.stderr


def test_req_preview_no_longer_sets_behavior_modes_or_builds():
    text=(ROOT/'scripts/ops/req-owner.sh').read_text()
    tail=text[text.index(' preview|preview-free)\n'):]
    assert 'PREVIEW_EXEC_PROVIDER' not in tail and 'PREVIEW_CHECKS' not in tail
    assert 'args+=(--apply)' in tail
