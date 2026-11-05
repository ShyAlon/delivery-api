"""Remote build is retired: alias must not upload source or contact a server.

Exact build contracts moved to test_exact_image_build.py. Publication and reuse
contracts moved to test_private_image_publication/test_image_receipt. A failed
candidate never causes a deploy-side rebuild. Smoke diagnostic still rejects
unrelated/malformed image tags before any SSH.
"""
from pathlib import Path
import os
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_remote_build_alias_is_local_only_and_forwards_arguments(tmp_path):
    docker=tmp_path/'scripts/docker';ops=tmp_path/'scripts/ops';binpath=tmp_path/'bin'
    docker.mkdir(parents=True);ops.mkdir(parents=True);binpath.mkdir()
    script=docker/'build-full-image.sh';script.write_bytes((ROOT/'scripts/docker/build-full-image.sh').read_bytes())
    output=tmp_path/'args'
    (ops/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    (binpath/'ssh').write_text('#!/bin/sh\nexit 99\n');(binpath/'ssh').chmod(0o755)
    result=subprocess.run(['bash',str(script),'--run-dir','/tmp/space path'],env=dict(os.environ,OUTPUT=str(output),PATH=str(binpath)+':'+os.environ['PATH']),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text().splitlines()==['--build','--run-dir','/tmp/space path']
    assert 'no server contact' in result.stderr


@pytest.mark.parametrize('bad',['ubuntu:latest','requi-full:x;rm -rf /','requi-full:',''])
def test_diagnostic_rejects_unrelated_or_malformed_tags_before_ssh(tmp_path,bad):
    binpath=tmp_path/'bin';binpath.mkdir()
    marker=tmp_path/'ssh-called'
    (binpath/'ssh').write_text('#!/bin/sh\ntouch "$MARKER"\nexit 99\n');(binpath/'ssh').chmod(0o755)
    result=subprocess.run(['bash',str(ROOT/'scripts/docker/smoke-image.sh'),bad],env=dict(os.environ,MARKER=str(marker),PATH=str(binpath)+':'+os.environ['PATH']),capture_output=True,text=True)
    assert result.returncode==2 and 'refusing' in result.stdout
    assert not marker.exists()
