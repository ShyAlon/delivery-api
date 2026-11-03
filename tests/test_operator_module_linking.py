"""Real module linking catches missing exports; syntax-only checks cannot."""
from pathlib import Path
import shutil,subprocess
ROOT=Path(__file__).parents[1]

def link(root):
    return subprocess.run(['node','--experimental-vm-modules',str(ROOT/'scripts/ci/check-operator-modules.mjs'),str(root)],text=True,capture_output=True)

def test_every_browser_module_links_without_dom_or_network():
    result=link(ROOT/'src/delivery_api')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'all imports resolve' in result.stdout

def test_missing_shared_export_fails_loudly(tmp_path):
    for name in ('web','backoffice_web'):shutil.copytree(ROOT/'src/delivery_api'/name,tmp_path/name)
    path=tmp_path/'web/operator-ui.js'
    path.write_text(path.read_text().replace('export function statePill(', 'function statePill('))
    result=link(tmp_path)
    assert result.returncode!=0
    assert 'statePill' in result.stderr and 'export' in result.stderr
