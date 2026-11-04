"""One local launcher, not an inspection-only fork.
The real single-launcher harness is test_preview_exec_preflight, including failures,
key secrecy, reviewed pins, build/smoke order and no work before confirmation.
"""
import os,subprocess
from pathlib import Path
ROOT=Path(__file__).parents[1]

def test_compatibility_preview_forwards_to_single_launcher_without_server_contact(tmp_path):
    ops=tmp_path/'ops';ops.mkdir()
    (ops/'preview.sh').write_text((ROOT/'scripts/ops/preview.sh').read_text())
    (ops/'preview-exec.sh').write_text('printf "single:%s\\n" "$1"; exit 17\n')
    result=subprocess.run(['bash',str(ops/'preview.sh'),'fixture'],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
    assert result.returncode==17 and result.stdout=='single:fixture\n'

def test_no_inspection_mode_or_server_branch_remains():
    source=(ROOT/'scripts/ops/preview.sh').read_text()
    assert 'ssh' not in source and 'docker' not in source
    assert 'preview-exec.sh' in source
