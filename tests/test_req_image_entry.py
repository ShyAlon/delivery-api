from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_req_owner_image_forwards_arguments_unchanged_before_generic_parser(tmp_path):
    scripts=tmp_path/'scripts/ops';scripts.mkdir(parents=True)
    (scripts/'req-owner.sh').write_bytes((ROOT/'scripts/ops/req-owner.sh').read_bytes())
    (scripts/'terminal-log.sh').write_text('# no log fixture\n')
    output=tmp_path/'args'
    (scripts/'image.sh').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$OUTPUT"\n')
    result=subprocess.run(['bash',str(scripts/'req-owner.sh'),'image','--review','--run-dir','/tmp/space path'],
                          env=dict(os.environ,OUTPUT=str(output)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.read_text().splitlines()==['--review','--run-dir','/tmp/space path']


def test_req_routes_image_to_same_clean_main_owner_entry():
    text=(ROOT/'scripts/ops/req').read_text()
    assert 'image|backoffice-access' in text and 'req image | backoffice-access' in text
    assert 'git -C "$REPO" fetch origin main' in text
